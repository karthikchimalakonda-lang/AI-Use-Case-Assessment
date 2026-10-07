# Evidence checklist

## Before publishing or submitting

- [x] Compartment, application, and model OCIDs removed from source and `func.yaml`.
- [x] Object Storage namespace, bucket name, and region replaced with placeholders.
- [x] Third-party vendor names in sample data replaced with generic terms.
- [x] No private keys, OCI config, `.env`, or OAuth files in the repository (`.gitignore` enforces this).
- [x] Unit tests pass and the test evidence was regenerated from the current code.
- [ ] Repository is public or accessible to ACE reviewers.
- [ ] README and Mermaid diagram render correctly on GitHub.

## Optional additional evidence

Mask OCIDs, tenancy, compartment, namespace, and bucket details in every screenshot.

1. OCI Functions application showing `usecase-ai-question-generator`.
2. Function configuration page with variable names visible and values masked.
3. Object Storage `UseCase_AI/questions/...` folder with generated question files.
4. A generated question JSON for a synthetic use case.
5. The assessment-taking screen and the results screen with explanations.

## Suggested demo flow (under 3 minutes)

1. Show a use case in the library: business requirement and configuration.
2. Trigger generation, or show the weekly refresh, and open the generated JSON.
3. Take the assessment as an employee and submit.
4. Show the score and explanations, then edit the use case and show that only that use case regenerates.
5. Close with the OCI services used and the benefit: always-current, zero-authoring assessments.
