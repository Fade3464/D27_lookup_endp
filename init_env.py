#!/usr/bin/env python3
"""Create local configuration with fresh secrets; never overwrite existing config."""
import os
from pathlib import Path
import secrets

root = Path(__file__).resolve().parent
content = (root / ".env.example").read_text()
content = content.replace("DJANGO_SECRET_KEY=\n", f"DJANGO_SECRET_KEY={secrets.token_hex(48)}\n")
content = content.replace("DB_PASSWORD=\n", f"DB_PASSWORD={secrets.token_urlsafe(36)}\n")
try:
    fd = os.open(root / ".env", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
except FileExistsError:
    print(".env already exists; left unchanged.")
else:
    with os.fdopen(fd, "w") as file:
        file.write(content)
    print("Created .env with fresh local secrets.")
