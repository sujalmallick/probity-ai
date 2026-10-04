"""Write the OpenAPI schema to docs/openapi.json (for typed web clients).   python -m probity.api.dump_openapi"""

import json

from probity.api.main import app
from probity.config import REPO_ROOT

if __name__ == "__main__":
    out = REPO_ROOT / "docs" / "openapi.json"
    out.write_text(json.dumps(app.openapi(), indent=1), encoding="utf-8")
    print(f"wrote {out} ({len(app.openapi()['paths'])} paths)")
