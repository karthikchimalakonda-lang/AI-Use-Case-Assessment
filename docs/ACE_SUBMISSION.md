# ACE Product Usage submission draft

## Suggested title

AI-Generated ERP Knowledge Assessments with OCI Functions and OCI Generative AI

## Contribution description

I built an AI-powered assessment engine on Oracle Cloud Infrastructure that turns an ERP use case library into employee knowledge assessments automatically. Use cases are organised by pillar (HCM, Finance, SCM, Manufacturing), module, and process. Each holds a business requirement and its external and internal configuration.

An OCI Function receives use cases from the Use Case Library application, or from a weekly scheduled refresh, and indexes them in a ChromaDB vector store persisted in OCI Object Storage. It uses checksum-based delta detection to find new or changed use cases. For each one, it calls OCI Generative AI (Cohere Command R+) with a structured prompt that returns multiple-choice questions, the correct answer, and an explanation as validated JSON. The question sets are stored in Object Storage and returned to the application. The application saves them in its database and serves randomised questions to employees through a PL/SQL procedure, keeping the answer key server-side for grading and feedback.

Unchanged content is never regenerated, which keeps model cost low, and a model failure on one use case doesn't stop the batch. The ChromaDB index also prepares the library for a future retrieval-augmented chatbot.

## Evidence to attach

- Repository link: https://github.com/karthikchimalakonda-lang/AI-Use-Case-Assessment
- `evidence/01-knowledge-assessments-dashboard.png` - assessments live in the Use Case Library application
- `evidence/02-unit-tests-passing.png` - offline unit tests for delta detection, parsing, storage, and handler flow

## Reviewer notes

Source code is sanitized: OCIDs, namespace, bucket, region, and endpoints are placeholders. Sample use cases are generic ERP scenarios with no client data or credentials.
