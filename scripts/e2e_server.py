"""Isolated fresh test database, never touches the interactive prototype's state."""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import uvicorn
from backend.app import create_app
if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='governed-builder-e2e-') as temp:
        app=create_app(str(Path(temp)/'state.sqlite'))
        uvicorn.run(app,host='127.0.0.1',port=5188,access_log=False)
