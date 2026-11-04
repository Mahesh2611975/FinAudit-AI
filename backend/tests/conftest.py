import os
import tempfile

TEST_DB_DIR = tempfile.mkdtemp(prefix="finaudit-pytest-")
os.environ["FINAUDIT_DATABASE_URL"] = f"sqlite:///{os.path.join(TEST_DB_DIR, 'test.db')}"
