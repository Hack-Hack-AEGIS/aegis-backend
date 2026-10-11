import os
import tempfile

# Tests must not write into the repo's data/ folder.
os.environ.setdefault("AEGIS_DATA_DIR", tempfile.mkdtemp(prefix="aegis-test-"))
