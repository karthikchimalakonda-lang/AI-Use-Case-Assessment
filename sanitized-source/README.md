# Sanitized source

| Folder | Role | Invoked by |
| --- | --- | --- |
| `usecase-question-generator` | Indexes use cases in ChromaDB, detects changes, generates MCQs with OCI Generative AI, persists them to Object Storage, and returns them to the app | Use Case Library application API, weekly schedule |

Sanitization applied:

- Compartment, Functions application, and Generative AI model OCIDs removed.
- Object Storage namespace and bucket replaced with `sample_namespace` and `sample-usecase-bucket`.
- Region-specific endpoint replaced with `<region>`.
- Third-party vendor names in the built-in sample payload replaced with generic terms.
- Authentication now tries resource principals first (the OCI Functions standard), then instance principals, then a local config file.

Set real values only in the function configuration in the OCI Console, never in Git.
