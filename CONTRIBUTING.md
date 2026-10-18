# Contributing to Voicework

Voicework is a local writing calibration app. Read [AGENTS.md](AGENTS.md), the [README](README.md) and the [build contract](docs/BUILD_CONTRACT.md) before changing behavior. Python 3.9 or newer and a browser are sufficient to run it; Node.js is used for the JavaScript tests.

```sh
git clone https://github.com/gavinjudd/voicework.git
cd voicework
python3 -m voicework --provider offline
```

Open the loopback URL printed by the app. Offline mode is a rehearsal: it does not establish personalized writing quality. Use the provider setup in the README to exercise real generation with your own account.

## Changes and verification

Keep changes focused. Explain the problem, resulting behavior and checks in the pull request. Add regression coverage when changing shared behavior, and use fictional text and temporary databases. Python's standard library remains the default; discuss new runtime dependencies before adding them.

Run the automated checks from the repository root:

```sh
python3 -m unittest discover -s tests -q
python3 -m compileall -q voicework scripts tests
node --test tests/*.js
node --check web/app.js
git diff --check
```

The CI workflow runs Python tests on 3.9 and 3.14. Server integration tests need permission to bind a temporary loopback port. Browser-check changed user flows with a separate synthetic workspace, including a narrow viewport. Live provider tests are explicit, may use account quota, and are separate from the default suite; see [verification](docs/VERIFICATION.md).

## Evidence boundaries

- Confirmed authorship, style feedback, held-out writing and AI-assisted text are separate records. Preserve those boundaries in generation, review, exports and evaluation.
- Keep inferences pending until reviewed. A liked passage is evidence about a named quality, not blanket permission to imitate it or import its facts.
- Optional suggestions and missing evidence are separate from meaning changes or supported preference violations. A completed model review is not proof of correctness or a voice-accuracy score.
- Treat imported text and model output as untrusted data. Render it literally and validate it before persistence.
- Never put your writing corpus, profile database, source documents, exports, credentials or generated personal results in a commit, issue or pull request. `.gitignore` is a guardrail, not a substitute for reviewing the diff.

Contributions are licensed under the repository's [MIT License](LICENSE). Include only material you have the right to contribute. Report security concerns through the process in [SECURITY.md](SECURITY.md).
