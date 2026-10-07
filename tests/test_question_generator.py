"""Offline unit tests for the UseCase AI question generator.

The OCI SDK, FDK, and ChromaDB are replaced with lightweight stubs so the
function logic (delta detection, GenAI response parsing, Object Storage keys,
handler flow) can be verified without cloud access.

    python -m unittest discover -s tests -v
"""

import copy
import importlib.util
import io
import json
import logging
import sys
import types
import unittest
from pathlib import Path

FUNC = Path(__file__).resolve().parent.parent / "sanitized-source" / "usecase-question-generator" / "func.py"


def install_stubs():
    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        sys.modules[name] = mod
        return mod

    class Response:
        def __init__(self, ctx, response_data=None, headers=None, status_code=200):
            self.body, self.status_code = json.loads(response_data), status_code

    class ServiceError(Exception):
        def __init__(self, status):
            self.status = status

    class Model:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    fdk = module("fdk")
    fdk.context = module("fdk.context", InvocationContext=object)
    fdk.response = module("fdk.response", Response=Response)
    oci = module("oci", exceptions=types.SimpleNamespace(ServiceError=ServiceError))
    module("oci.object_storage", ObjectStorageClient=object)
    module("oci.generative_ai_inference", GenerativeAiInferenceClient=object)
    module("oci.generative_ai_inference.models",
           CohereChatRequest=Model, OnDemandServingMode=Model, ChatDetails=Model)
    module("chromadb", PersistentClient=object, Client=object)
    module("chromadb.config", Settings=Model)
    return oci


install_stubs()
spec = importlib.util.spec_from_file_location("question_generator", FUNC)
fn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fn)
logging.disable(logging.CRITICAL)  # keep test output readable; failures are asserted, not logged


def canned_questions(n=5):
    return [{"question_no": i, "question": f"Q{i}?", "options": {"A": "a", "B": "b", "C": "c", "D": "d"},
             "correct_option": "A", "explanation": "Because."} for i in range(1, n + 1)]


class FakeGenAI:
    def __init__(self, text):
        self.text, self.calls = text, []

    def chat(self, details):
        self.calls.append(details)
        return types.SimpleNamespace(data=types.SimpleNamespace(
            chat_response=types.SimpleNamespace(text=self.text)))


class FakeObjectStorage:
    def __init__(self):
        self.objects = {}

    def get_object(self, namespace, bucket, key):
        if key not in self.objects:
            raise fn.oci.exceptions.ServiceError(404)
        return types.SimpleNamespace(data=types.SimpleNamespace(content=self.objects[key]))

    def put_object(self, namespace, bucket, key, put_object_body, content_type):
        self.objects[key] = put_object_body.read()


class FakeCollection:
    def __init__(self):
        self.ids = []

    def upsert(self, ids, documents, metadatas):
        self.ids = ids


class FakeChroma:
    def __init__(self):
        self.collection = FakeCollection()

    def get_or_create_collection(self, name, metadata):
        return self.collection


class DeltaDetectionTests(unittest.TestCase):
    def setUp(self):
        self.processes = copy.deepcopy(fn.SAMPLE_PAYLOAD["processes"])

    def test_all_usecases_new_on_first_run(self):
        self.assertEqual(len(fn.filter_changed_usecases(self.processes, {})), 5)

    def test_unchanged_usecases_skipped(self):
        meta = {uc["case_id"]: {"checksum": fn.compute_checksum(uc)}
                for proc in self.processes for uc in proc["usecases"]}
        self.assertEqual(fn.filter_changed_usecases(self.processes, meta), [])

    def test_only_edited_usecase_regenerated(self):
        meta = {uc["case_id"]: {"checksum": fn.compute_checksum(uc)}
                for proc in self.processes for uc in proc["usecases"]}
        self.processes[1]["usecases"][0]["internal_config"] += " Minimum score threshold now 70%."
        changed = fn.filter_changed_usecases(self.processes, meta)
        self.assertEqual([c["usecase"]["case_id"] for c in changed], ["UC-REC-002-01"])


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.proc = fn.SAMPLE_PAYLOAD["processes"][0]
        self.uc = self.proc["usecases"][0]

    def test_parses_plain_json(self):
        genai = FakeGenAI(json.dumps(canned_questions()))
        questions = fn.generate_questions_for_usecase(genai, "HCM", "Recruiting", self.proc["process_name"], self.uc)
        self.assertEqual(len(questions), 5)
        prompt = genai.calls[0].chat_request.message
        self.assertIn(self.uc["title"], prompt)
        self.assertIn("exactly 5 multiple-choice questions", prompt)

    def test_strips_markdown_fences(self):
        genai = FakeGenAI("```json\n" + json.dumps(canned_questions(2)) + "\n```")
        questions = fn.generate_questions_for_usecase(genai, "HCM", "Recruiting", "p", self.uc, n=2)
        self.assertEqual([q["correct_option"] for q in questions], ["A", "A"])

    def test_question_object_key_layout(self):
        storage = FakeObjectStorage()
        key = fn.persist_questions(storage, "HCM", "Recruiting", "UC-1", "PROC-1", canned_questions(1), self.uc)
        self.assertEqual(key, "UseCase_AI/questions/HCM/Recruiting/PROC-1/UC-1.json")
        self.assertEqual(json.loads(storage.objects[key])["title"], self.uc["title"])


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.storage, self.chroma = FakeObjectStorage(), FakeChroma()
        self.genai = FakeGenAI(json.dumps(canned_questions()))
        fn.get_signer = lambda: object()
        fn.get_object_storage_client = lambda signer: self.storage
        fn.get_genai_client = lambda signer: self.genai
        fn.load_chroma_from_os = lambda os_client: self.chroma
        fn.save_chroma_to_os = lambda os_client: None

    def invoke(self, body):
        return fn.handler(None, io.BytesIO(json.dumps(body).encode()))

    def test_sample_data_mode(self):
        self.assertEqual(self.invoke({"mode": "sample_data"}).body["module"], "Recruiting")

    def test_empty_payload_rejected(self):
        self.assertEqual(self.invoke({"pillar": "HCM"}).status_code, 400)

    def test_weekly_refresh_then_up_to_date(self):
        first = self.invoke(fn.SAMPLE_PAYLOAD).body
        self.assertEqual(first["status"], "success")
        self.assertEqual(first["total_usecases_processed"], 5)
        self.assertEqual(sum(r["questions_generated"] for r in first["results"]), 25)
        self.assertEqual(len(self.chroma.collection.ids), 5)
        self.assertIn(fn.META_KEY, self.storage.objects)

        second = self.invoke(fn.SAMPLE_PAYLOAD).body
        self.assertEqual(second["status"], "up_to_date")
        self.assertEqual(len(self.genai.calls), 5)  # no new GenAI calls on unchanged content

    def test_genai_failure_is_isolated(self):
        self.genai.text = "not json"
        body = self.invoke(fn.SAMPLE_PAYLOAD).body
        self.assertEqual(body["status"], "success")
        self.assertTrue(all(r["questions_generated"] == 0 for r in body["results"]))


if __name__ == "__main__":
    unittest.main()
