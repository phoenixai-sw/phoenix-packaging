"""Export the real app contract without connecting to configured services.

Run from repository root: python packages/contracts/export_openapi.py [--check]
Only an isolated in-memory database engine and temporary local storage are built.
The FastAPI lifespan is not entered, so no schema/data is created or mutated.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@contextmanager
def isolated_environment(directory):
    values = {'APP_ENV': 'test', 'DATABASE_URL': 'sqlite:///:memory:',
              'STORAGE_BACKEND': 'local', 'STORAGE_DIR': directory,
              'AI_PROVIDER': 'disabled', 'PAYMENT_PROVIDER': 'disabled',
              'ENABLE_PRODUCTION_EXPORT': 'false', 'GOOGLE_CLIENT_ID': '',
              'ADMIN_EMAILS': '', 'WORKER_SECRET': '', 'DEMO_MODE': 'true',
              'IMAGE_MODEL': 'gpt-image-2.5-sunburst',
              'AI_IMAGE_MODELS': 'gpt-image-2.5-sunburst,gpt-image-2.5-flare'}
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def document():
    with TemporaryDirectory(prefix='phoenix-openapi-') as directory:
        with isolated_environment(directory):
            from services.api.main import create_app
            from services.api.config import Settings
            app = create_app(Settings())
            result = app.openapi()
            app.state.engine.dispose()
    return result


def render():
    return json.dumps(document(), ensure_ascii=False, indent=2, sort_keys=True) + '\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    target = Path(__file__).with_name('openapi.json')
    content = render()
    if args.check:
        if not target.exists() or target.read_text(encoding='utf-8') != content:
            raise SystemExit('Stale API contract: run python packages/contracts/export_openapi.py')
    else:
        target.write_text(content, encoding='utf-8', newline='\n')
    print('API OpenAPI contract is current.' if args.check else 'API OpenAPI contract generated.')
