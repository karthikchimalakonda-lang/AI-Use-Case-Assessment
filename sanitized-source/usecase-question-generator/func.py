"""
OCI Function 1: UseCase AI - Question Generator
Application : UseCase AI (OCI Functions application)
Bucket      : configured via OCI_BUCKET / OCI_NAMESPACE → folder: UseCase_AI/
Trigger     : HTTP invoke (from app) OR weekly scheduled invoke
Purpose     :
  1. Receive payload with pillar/module/process/usecases (or run weekly refresh).
  2. Upsert usecases into ChromaDB (persisted as .zip in Object Storage).
  3. For each usecase, call OCI GenAI (Cohere Command-R) to generate
     5 MCQs (4 options, 1 correct) with explanation.
  4. Store questions + correct answers + explanations as JSON in Object Storage.
  5. Return the full question set to the caller so the app can persist
     them into its DB table via a PL/SQL procedure.

Design decision (RECOMMENDED / PROD-GRADE):
  - Generate questions + correct answer + explanation in THIS single function.
  - Store everything (questions, options, correct answer, explanation) in
    Object Storage AND return to the app API.
  - App API stores in DB; PL/SQL RANDOM procedure serves only questions +
    options to end-user (hides correct answer).
  - On submission the app API reads correct_answer / explanation from its
    own DB table — no second OCI function needed for validation.
  - ChromaDB is maintained here for future chatbot / RAG use cases.
"""

import io
import json
import logging
import os
import zipfile
import datetime
import hashlib
import traceback
from typing import Any

import fdk.context
import fdk.response

# ── OCI SDK ──────────────────────────────────────────────────────────────────
import oci
from oci.object_storage import ObjectStorageClient
from oci.generative_ai_inference import GenerativeAiInferenceClient
from oci.generative_ai_inference.models import (
    CohereChatRequest,
    OnDemandServingMode,
    ChatDetails,
)

# ── ChromaDB ──────────────────────────────────────────────────────────────────
import chromadb
from chromadb.config import Settings

# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("usecase_ai.fn1")

# ── Environment / Constants ───────────────────────────────────────────────────
NAMESPACE          = os.environ.get("OCI_NAMESPACE", "sample_namespace")
BUCKET_NAME        = os.environ.get("OCI_BUCKET", "sample-usecase-bucket")
BUCKET_PREFIX      = "UseCase_AI"
CHROMA_ZIP_KEY     = f"{BUCKET_PREFIX}/chromadb/chroma_store.zip"
QUESTIONS_PREFIX   = f"{BUCKET_PREFIX}/questions"
META_KEY           = f"{BUCKET_PREFIX}/meta/usecase_meta.json"
GENAI_ENDPOINT     = os.environ.get(
    "OCI_GENAI_ENDPOINT",
    "https://inference.generativeai.<region>.oci.oraclecloud.com"
)
GENAI_MODEL_ID     = os.environ.get(
    "OCI_GENAI_MODEL_ID",
    "cohere.command-r-plus"           # OCI GenAI Cohere Command-R+
)
COMPARTMENT_ID     = os.environ.get(
    "OCI_COMPARTMENT_ID",
    ""
)
QUESTIONS_PER_CASE = int(os.environ.get("QUESTIONS_PER_CASE", "5"))
CHROMA_LOCAL_DIR   = "/tmp/chroma_store"


# ═══════════════════════════════════════════════════════════════════════════════
#  SAMPLE DATA  (returned when invoked with {"mode": "sample_data"})
# ═══════════════════════════════════════════════════════════════════════════════
SAMPLE_PAYLOAD = {
    "pillar": "HCM",
    "module": "Recruiting",
    "triggered_by": "weekly_refresh",         # or "user_assessment"
    "requested_by_case_id": None,             # set when user clicks "Take Assessment"
    "processes": [
        {
            "process_id": "PROC-REC-001",
            "process_name": "Job Requisition Management",
            "usecases": [
                {
                    "case_id": "UC-REC-001-01",
                    "title": "Create Job Requisition",
                    "business_usecase": (
                        "HR Manager creates a job requisition when a new vacancy "
                        "arises due to attrition or business expansion. The system "
                        "validates headcount budget, captures job details, grade, "
                        "department, and routes for approval via configurable workflow."
                    ),
                    "external_config": (
                        "Requisition approval workflow configured with 3 levels: "
                        "Line Manager → HR BP → Finance. Auto-approval threshold "
                        "set for grade below G5. Integration with external "
                        "job boards via REST API."
                    ),
                    "internal_config": (
                        "Headcount table linked to cost-centre. Budget validation "
                        "rule fires on submit. Notification templates configured in "
                        "BIP for approver emails. Role-based access: Recruiter, "
                        "HR Manager, Finance Controller."
                    ),
                    "last_updated": "2026-07-01T10:00:00Z",
                    "version": 3
                },
                {
                    "case_id": "UC-REC-001-02",
                    "title": "Requisition Approval & Rejection Flow",
                    "business_usecase": (
                        "Approvers receive bell notification and email. They can "
                        "approve, reject with comments, or delegate. On final "
                        "approval the requisition status changes to 'Open' and "
                        "triggers posting to configured job boards."
                    ),
                    "external_config": (
                        "Delegation rules set per role. Escalation timer: 48 hours "
                        "before auto-escalate to next level. Reject notification "
                        "sends back to initiator with mandatory comment."
                    ),
                    "internal_config": (
                        "BPM workflow with parallel vs sequential mode toggle. "
                        "Notification channel: In-App + Email. Audit trail captured "
                        "in requisition history tab."
                    ),
                    "last_updated": "2026-06-20T08:30:00Z",
                    "version": 2
                }
            ]
        },
        {
            "process_id": "PROC-REC-002",
            "process_name": "Candidate Sourcing & Application",
            "usecases": [
                {
                    "case_id": "UC-REC-002-01",
                    "title": "External Career Site Application",
                    "business_usecase": (
                        "Candidates search and apply for positions via the external "
                        "career portal. They create a profile, upload resume, answer "
                        "screening questions, and submit application. System deduplicates "
                        "candidate profiles based on email."
                    ),
                    "external_config": (
                        "Career site branded with company theme. GDPR consent banner "
                        "enabled. Resume parsing via third-party AI parser. "
                        "Job board syndication to configured external job boards."
                    ),
                    "internal_config": (
                        "Screening questionnaire configured per requisition template. "
                        "Minimum score threshold 60% to advance. Duplicate check on "
                        "email + phone. Auto-acknowledgement email on submission."
                    ),
                    "last_updated": "2026-07-05T14:00:00Z",
                    "version": 1
                },
                {
                    "case_id": "UC-REC-002-02",
                    "title": "Employee Referral Submission",
                    "business_usecase": (
                        "Employees refer external candidates for open positions and "
                        "track referral status. Referral bonus is triggered automatically "
                        "when referred candidate completes 90-day probation."
                    ),
                    "external_config": (
                        "Referral portal accessible from employee self-service. "
                        "Email invite sent to referred candidate with tracking token. "
                        "Bonus payout integrated with Payroll module."
                    ),
                    "internal_config": (
                        "Referral bonus amount configured per grade band. 90-day "
                        "timer starts from offer accept date. Payroll element "
                        "'Referral Bonus' mapped in compensation config."
                    ),
                    "last_updated": "2026-05-15T09:00:00Z",
                    "version": 2
                }
            ]
        },
        {
            "process_id": "PROC-REC-003",
            "process_name": "Interview & Selection",
            "usecases": [
                {
                    "case_id": "UC-REC-003-01",
                    "title": "Interview Scheduling & Feedback",
                    "business_usecase": (
                        "Recruiter schedules interview panels, system sends calendar "
                        "invites. Interviewers fill structured feedback forms. "
                        "Hiring committee reviews consolidated feedback and decides "
                        "advance/reject/hold."
                    ),
                    "external_config": (
                        "Calendar sync with corporate mail via connector. "
                        "Video interview integration with a meeting platform. Structured feedback "
                        "form with competency rating 1-5 and mandatory comments."
                    ),
                    "internal_config": (
                        "Interview panel composition rules per job family. Feedback "
                        "submission deadline: 24 hours post interview. Auto-reminder "
                        "at T-1 hour. Hiring committee role configured per business unit."
                    ),
                    "last_updated": "2026-07-08T11:00:00Z",
                    "version": 4
                }
            ]
        }
    ]
}


# ═══════════════════════════════════════════════════════════════════════════════
#  OCI CLIENT HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def get_signer():
    """Use resource principal (OCI Functions) or instance principal auth; fall back to config for local dev."""
    try:
        signer = oci.auth.signers.get_resource_principals_signer()
        logger.info("Resource principal signer obtained.")
        return signer
    except Exception as exc:
        logger.info(f"Resource principal unavailable ({exc}); trying instance principal.")
    try:
        logger.info("Attempting instance principal signer...")
        signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        logger.info("Instance principal signer obtained.")
        return signer
    except Exception as exc:
        logger.warning(f"Instance principal failed ({exc}); using ~/.oci/config")
        config = oci.config.from_file()
        return config


def get_object_storage_client(signer):
    if isinstance(signer, dict):
        return ObjectStorageClient(signer)
    return ObjectStorageClient({}, signer=signer)


def get_genai_client(signer):
    if isinstance(signer, dict):
        return GenerativeAiInferenceClient(
            signer, service_endpoint=GENAI_ENDPOINT
        )
    return GenerativeAiInferenceClient(
        {}, signer=signer, service_endpoint=GENAI_ENDPOINT
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  OBJECT STORAGE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def read_object(os_client, key: str) -> bytes | None:
    try:
        resp = os_client.get_object(NAMESPACE, BUCKET_NAME, key)
        data = resp.data.content
        logger.info(f"[OS] Read {key} ({len(data)} bytes)")
        return data
    except oci.exceptions.ServiceError as e:
        if e.status == 404:
            logger.info(f"[OS] Object not found: {key}")
            return None
        raise


def write_object(os_client, key: str, data: bytes, content_type="application/json"):
    os_client.put_object(
        NAMESPACE, BUCKET_NAME, key,
        put_object_body=io.BytesIO(data),
        content_type=content_type
    )
    logger.info(f"[OS] Written {key} ({len(data)} bytes)")


# ═══════════════════════════════════════════════════════════════════════════════
#  CHROMADB HELPERS  (persisted as zip in Object Storage)
# ═══════════════════════════════════════════════════════════════════════════════

def load_chroma_from_os(os_client) -> chromadb.Client:
    """Download chroma zip from OS, unzip to /tmp, return client."""
    import shutil
    if os.path.exists(CHROMA_LOCAL_DIR):
        shutil.rmtree(CHROMA_LOCAL_DIR)
    os.makedirs(CHROMA_LOCAL_DIR, exist_ok=True)

    zip_bytes = read_object(os_client, CHROMA_ZIP_KEY)
    if zip_bytes:
        logger.info("[Chroma] Unpacking existing store from Object Storage...")
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.extractall(CHROMA_LOCAL_DIR)
    else:
        logger.info("[Chroma] No existing store — fresh ChromaDB initialised.")

    client = chromadb.PersistentClient(
        path=CHROMA_LOCAL_DIR,
        settings=Settings(anonymized_telemetry=False)
    )
    return client


def save_chroma_to_os(os_client):
    """Zip /tmp/chroma_store and upload to Object Storage."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(CHROMA_LOCAL_DIR):
            for fname in files:
                abs_path = os.path.join(root, fname)
                arc_name = os.path.relpath(abs_path, CHROMA_LOCAL_DIR)
                zf.write(abs_path, arc_name)
    buf.seek(0)
    write_object(os_client, CHROMA_ZIP_KEY, buf.read(), content_type="application/zip")
    logger.info("[Chroma] Store persisted to Object Storage.")


def upsert_usecases_to_chroma(chroma_client, processes: list):
    """Upsert all usecases from payload into ChromaDB collection."""
    collection = chroma_client.get_or_create_collection(
        name="usecases",
        metadata={"hnsw:space": "cosine"}
    )
    ids, docs, metas = [], [], []
    for proc in processes:
        for uc in proc.get("usecases", []):
            doc_text = (
                f"Title: {uc['title']}\n"
                f"Process: {proc['process_name']}\n"
                f"Business Usecase: {uc['business_usecase']}\n"
                f"External Config: {uc['external_config']}\n"
                f"Internal Config: {uc['internal_config']}"
            )
            ids.append(uc["case_id"])
            docs.append(doc_text)
            metas.append({
                "process_id": proc["process_id"],
                "process_name": proc["process_name"],
                "title": uc["title"],
                "last_updated": uc.get("last_updated", ""),
                "version": str(uc.get("version", 1))
            })
    collection.upsert(ids=ids, documents=docs, metadatas=metas)
    logger.info(f"[Chroma] Upserted {len(ids)} usecases into collection 'usecases'.")
    return collection


# ═══════════════════════════════════════════════════════════════════════════════
#  USECASE META  (tracks last_updated vs last_processed for delta detection)
# ═══════════════════════════════════════════════════════════════════════════════

def load_meta(os_client) -> dict:
    data = read_object(os_client, META_KEY)
    if data:
        return json.loads(data)
    return {}   # {case_id: {"last_updated": "...", "last_processed": "...", "checksum": "..."}}


def save_meta(os_client, meta: dict):
    write_object(os_client, META_KEY, json.dumps(meta, indent=2).encode())


def compute_checksum(uc: dict) -> str:
    content = uc.get("business_usecase","") + uc.get("external_config","") + uc.get("internal_config","")
    return hashlib.md5(content.encode()).hexdigest()


def filter_changed_usecases(processes: list, meta: dict) -> list:
    """Return only usecases that are new or changed since last processing."""
    changed = []
    for proc in processes:
        for uc in proc.get("usecases", []):
            cid = uc["case_id"]
            checksum = compute_checksum(uc)
            if cid not in meta or meta[cid].get("checksum") != checksum:
                logger.info(f"[Delta] Usecase {cid} is NEW or CHANGED — will regenerate questions.")
                changed.append({"process": proc, "usecase": uc, "checksum": checksum})
            else:
                logger.info(f"[Delta] Usecase {cid} UNCHANGED — skipping question generation.")
    return changed


# ═══════════════════════════════════════════════════════════════════════════════
#  GENAI  —  QUESTION GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

QUESTION_PROMPT_TEMPLATE = """
You are an expert Oracle HCM/SCM/Finance implementation consultant and trainer.

Below is a detailed usecase from the {pillar} pillar, {module} module, 
process "{process_name}":

TITLE: {title}
BUSINESS USECASE: {business_usecase}
EXTERNAL CONFIGURATION: {external_config}
INTERNAL CONFIGURATION: {internal_config}

Generate exactly {n} multiple-choice questions to test a consultant's understanding 
of this usecase. Each question must:
1. Be directly based on the content above.
2. Have exactly 4 options labeled A, B, C, D.
3. Have exactly ONE correct answer.
4. Include a brief explanation (2-3 sentences) for the correct answer.

Respond ONLY with a valid JSON array (no markdown, no preamble) in this exact format:
[
  {{
    "question_no": 1,
    "question": "...",
    "options": {{
      "A": "...",
      "B": "...",
      "C": "...",
      "D": "..."
    }},
    "correct_option": "A",
    "explanation": "..."
  }}
]
"""


def generate_questions_for_usecase(
    genai_client, pillar: str, module: str,
    process_name: str, uc: dict, n: int = QUESTIONS_PER_CASE
) -> list:
    prompt = QUESTION_PROMPT_TEMPLATE.format(
        pillar=pillar,
        module=module,
        process_name=process_name,
        title=uc["title"],
        business_usecase=uc["business_usecase"],
        external_config=uc["external_config"],
        internal_config=uc["internal_config"],
        n=n
    )
    logger.info(f"[GenAI] Generating {n} questions for case_id={uc['case_id']} ...")

    chat_request = CohereChatRequest(
        message=prompt,
        max_tokens=2048,
        temperature=0.3,
        is_stream=False
    )
    chat_details = ChatDetails(
        compartment_id=COMPARTMENT_ID,
        serving_mode=OnDemandServingMode(model_id=GENAI_MODEL_ID),
        chat_request=chat_request
    )

    response = genai_client.chat(chat_details)
    raw_text = response.data.chat_response.text.strip()

    # Strip markdown fences if model wraps response
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
    raw_text = raw_text.strip()

    questions = json.loads(raw_text)
    logger.info(f"[GenAI] Received {len(questions)} questions for {uc['case_id']}")
    return questions


# ═══════════════════════════════════════════════════════════════════════════════
#  PERSIST QUESTIONS TO OBJECT STORAGE
# ═══════════════════════════════════════════════════════════════════════════════

def persist_questions(os_client, pillar: str, module: str, case_id: str,
                      process_id: str, questions: list, uc: dict):
    key = f"{QUESTIONS_PREFIX}/{pillar}/{module}/{process_id}/{case_id}.json"
    payload = {
        "case_id": case_id,
        "pillar": pillar,
        "module": module,
        "process_id": process_id,
        "title": uc["title"],
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "questions": questions
    }
    write_object(os_client, key, json.dumps(payload, indent=2).encode())
    logger.info(f"[OS] Questions for {case_id} persisted at {key}")
    return key


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

def handler(ctx: fdk.context.InvocationContext, data: io.BytesIO = None):
    logger.info("=" * 60)
    logger.info("UseCase AI — Function 1: Question Generator STARTED")
    logger.info(f"Invocation time: {datetime.datetime.utcnow().isoformat()}Z")

    try:
        # ── Parse Input ──────────────────────────────────────────────────────
        body = {}
        if data:
            raw = data.read()
            if raw:
                body = json.loads(raw)
                logger.info(f"[Input] Received payload keys: {list(body.keys())}")

        # Return sample data if requested
        if body.get("mode") == "sample_data":
            logger.info("[Mode] Returning sample data payload.")
            return fdk.response.Response(
                ctx,
                response_data=json.dumps(SAMPLE_PAYLOAD, indent=2),
                headers={"Content-Type": "application/json"}
            )

        pillar     = body.get("pillar", "HCM")
        module     = body.get("module", "Recruiting")
        processes  = body.get("processes", [])
        triggered_by   = body.get("triggered_by", "weekly_refresh")
        requested_case = body.get("requested_by_case_id")  # nullable

        if not processes:
            logger.warning("[Input] No processes/usecases in payload.")
            return fdk.response.Response(
                ctx,
                response_data=json.dumps({"status": "no_data", "message": "No processes provided."}),
                status_code=400,
                headers={"Content-Type": "application/json"}
            )

        logger.info(f"[Input] pillar={pillar}, module={module}, "
                    f"processes={len(processes)}, triggered_by={triggered_by}, "
                    f"requested_case={requested_case}")

        # ── OCI Clients ───────────────────────────────────────────────────────
        signer      = get_signer()
        os_client   = get_object_storage_client(signer)
        genai_client = get_genai_client(signer)

        # ── ChromaDB: load, upsert, save ──────────────────────────────────────
        logger.info("[Chroma] Loading ChromaDB from Object Storage...")
        chroma_client = load_chroma_from_os(os_client)
        upsert_usecases_to_chroma(chroma_client, processes)
        save_chroma_to_os(os_client)

        # ── Delta Detection ───────────────────────────────────────────────────
        meta = load_meta(os_client)
        changed_items = filter_changed_usecases(processes, meta)

        if not changed_items:
            logger.info("[Delta] No usecases changed — nothing to regenerate.")
            return fdk.response.Response(
                ctx,
                response_data=json.dumps({
                    "status": "up_to_date",
                    "message": "All usecases are up-to-date. No question regeneration needed."
                }),
                headers={"Content-Type": "application/json"}
            )

        # ── If user clicked "Take Assessment" for a specific case_id ──────────
        # Only regenerate that specific case; serve all questions for the module.
        if requested_case:
            logger.info(f"[Mode] User-triggered assessment for case_id={requested_case}")
            changed_items = [
                item for item in changed_items
                if item["usecase"]["case_id"] == requested_case
            ] or changed_items   # if not changed, will still serve existing from OS

        # ── Generate Questions for Changed Usecases ───────────────────────────
        all_results = []
        now_iso = datetime.datetime.utcnow().isoformat() + "Z"

        for item in changed_items:
            proc = item["process"]
            uc   = item["usecase"]
            cid  = uc["case_id"]

            try:
                questions = generate_questions_for_usecase(
                    genai_client=genai_client,
                    pillar=pillar,
                    module=module,
                    process_name=proc["process_name"],
                    uc=uc,
                    n=QUESTIONS_PER_CASE
                )
            except Exception as ge:
                logger.error(f"[GenAI] Failed for {cid}: {ge}\n{traceback.format_exc()}")
                questions = []

            obj_key = persist_questions(
                os_client, pillar, module, cid,
                proc["process_id"], questions, uc
            )

            # Update meta
            meta[cid] = {
                "last_updated": uc.get("last_updated", now_iso),
                "last_processed": now_iso,
                "checksum": item["checksum"],
                "object_key": obj_key
            }

            all_results.append({
                "case_id": cid,
                "title": uc["title"],
                "process_id": proc["process_id"],
                "process_name": proc["process_name"],
                "questions_generated": len(questions),
                "object_key": obj_key,
                "questions": questions    # returned to app API for DB storage
            })

        save_meta(os_client, meta)

        response_body = {
            "status": "success",
            "pillar": pillar,
            "module": module,
            "triggered_by": triggered_by,
            "processed_at": now_iso,
            "total_usecases_processed": len(changed_items),
            "results": all_results
        }

        logger.info(f"[Done] {len(changed_items)} usecases processed. "
                    f"Total questions: {sum(r['questions_generated'] for r in all_results)}")
        logger.info("=" * 60)

        return fdk.response.Response(
            ctx,
            response_data=json.dumps(response_body, indent=2),
            headers={"Content-Type": "application/json"}
        )

    except Exception as exc:
        logger.error(f"[FATAL] Unhandled exception: {exc}\n{traceback.format_exc()}")
        return fdk.response.Response(
            ctx,
            response_data=json.dumps({"status": "error", "message": str(exc)}),
            status_code=500,
            headers={"Content-Type": "application/json"}
        )
