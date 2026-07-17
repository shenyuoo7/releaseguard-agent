# ReleaseGuard synchronous API

The current FastAPI app exposes a small local surface and reuses the same
`ReleaseReviewService` as the CLI. It does not use a database, queue, background
worker, or real LLM request.

Start it from the repository root:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m uvicorn releaseguard_agent.api.app:app --host 127.0.0.1 --port 8000
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Run a deterministic review under the default allowed repository root:

```powershell
$body = @{
    project_path = "E:\A_project\Agent\ReleaseGuard_Agent\sample_projects\clean_python_project"
    include_pytest_execution = $false
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/reviews -ContentType "application/json" -Body $body
```

`POST /verifications` performs a real synchronous comparison. The caller
supplies a preserved baseline snapshot and a user-modified snapshot; the API
rescans both and returns resolved, new, and unchanged finding IDs. ReleaseGuard
never modifies either directory.

```powershell
$verification = @{
    before_project_path = "E:\A_project\Agent\ReleaseGuard_Agent\sample_projects\fastapi_bad_project"
    after_project_path = "E:\A_project\Agent\ReleaseGuard_Agent\sample_projects\fastapi_good_project"
    include_pytest_execution = $false
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/verifications -ContentType "application/json" -Body $verification
```

The module-level app allows only paths below the ReleaseGuard repository root.
Embedding the app for another trusted root requires explicit construction:

```python
from pathlib import Path

from releaseguard_agent.api import create_app

app = create_app(allowed_project_roots=[Path("E:/trusted-projects")])
```
