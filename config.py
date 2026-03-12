import os
from pathlib import Path

OBSIDIAN_VAULT = Path(os.getenv("OBSIDIAN_VAULT", "/app/obsidian_vault"))
MEMORY_PATH = Path("/app/memory")
DATA_PATH = Path("/app/data")