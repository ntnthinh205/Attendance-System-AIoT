"""FastAPI recognition/gallery/attendance server for the ESP32 hybrid firmware."""
import argparse
from contextlib import asynccontextmanager, closing
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat
from starlette.concurrency import run_in_threadpool
import uvicorn

from face_database import FaceDatabase, face_vector
from hybrid_gallery import gallery


class RecognitionRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra='allow')
    model: Literal['MBF_S8_V1'] = 'MBF_S8_V1'
    faces: Annotated[int, Field(ge=1, le=1)] = 1
    dimensions: Annotated[int, Field(ge=512, le=512)] = 512
    embedding: Annotated[list[FiniteFloat], Field(min_length=512, max_length=512)]
    quality: dict | None = None


Identifier = Annotated[str, Field(min_length=1, max_length=128)]


class AttendanceEvent(BaseModel):
    model_config = ConfigDict(strict=True, extra='allow')
    event_id: Identifier
    device_id: Identifier
    employee_id: Identifier
    gallery_revision: Identifier
    source: Literal['edge', 'server']
    similarity: Annotated[FiniteFloat, Field(ge=-1, le=1)]
    timestamp: int | None = None
    time_quality: Literal['system_clock', 'unsynced'] | None = None
    uptime_ms: Annotated[int, Field(ge=0)] | None = None


class EventAck(BaseModel):
    ack: str


def create_app(database_path, events_path, token, threshold=.5, margin=.05):
    """Application factory; importing this module never opens the production database."""
    if not isinstance(token, str) or not token or not token.isascii() or any(ord(c)<33 or ord(c)>126 for c in token):
        raise ValueError('A non-empty ASCII API token is required')
    database_path, events_path = Path(database_path), Path(events_path)
    # Check policy at startup even when the gallery is empty.
    import math
    if not math.isfinite(threshold) or not -1 <= threshold <= 1 or not math.isfinite(margin) or not 0 <= margin <= 2:
        raise ValueError('Invalid threshold/margin')

    @asynccontextmanager
    async def lifespan(app):
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(events_path)) as connection:
            with connection:
                connection.execute('CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, received_at TEXT DEFAULT CURRENT_TIMESTAMP)')
        yield

    app = FastAPI(title='ESP32 Hybrid Face Recognition', version='1.0.0', lifespan=lifespan,
                  description='Server matching, offline gallery distribution, and idempotent attendance sync. Use Authorize with api_token from edge_config.json.')
    bearer = HTTPBearer(auto_error=False)

    def authorize(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials is None or credentials.scheme.lower()!='bearer' or not secrets.compare_digest(credentials.credentials.encode('utf-8'), token.encode('ascii')):
            raise HTTPException(401, 'Unauthorized', headers={'WWW-Authenticate':'Bearer'})

    @app.middleware('http')
    async def request_limits(request: Request, call_next):
        if request.method=='POST':
            try:
                length=int(request.headers.get('content-length','0'))
            except ValueError:
                return JSONResponse({'error':'Invalid Content-Length'}, status_code=400)
            if length<0 or length>32768:
                return JSONResponse({'error':'Request body exceeds 32 KB'}, status_code=413)
            body=await request.body()
            if not body or len(body)>32768:
                return JSONResponse({'error':'Expected a JSON body of at most 32 KB'}, status_code=413)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({'error':exc.detail}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Avoid returning full biometric vectors or non-JSON NaN values in validation errors.
        details=[{key:error[key] for key in ('loc','msg','type')} for error in exc.errors()]
        return JSONResponse({'error':'Invalid request', 'details':details}, status_code=422)

    @app.get('/health', tags=['Service'])
    def health():
        return {'status':'ok', 'service':'esp32-hybrid', 'framework':'FastAPI'}

    secure=[Depends(authorize)]

    @app.get('/v1/gallery', dependencies=secure, tags=['Recognition'],
             response_class=Response, responses={200:{'content':{'application/octet-stream':{}}}})
    def get_gallery():
        try:
            binary, _, _=gallery(FaceDatabase(database_path), threshold, margin)
            return Response(binary, media_type='application/octet-stream', headers={'Cache-Control':'no-store'})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(503, 'Face gallery unavailable') from exc

    @app.post('/v1/recognize', dependencies=secure, tags=['Recognition'])
    def recognize(data: RecognitionRequest):
        payload=data.model_dump(exclude_none=True)
        try:
            face_vector(payload)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            _, db, revision=gallery(FaceDatabase(database_path), threshold, margin)
            result=db.recognize(payload, threshold, margin)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(503, 'Face gallery unavailable') from exc
        result.update(source='server', gallery_revision=revision)
        return result

    def store_event(data):
        # Preserve the exact received JSON types/extra fields for compatibility with queued events
        # and SQLite payloads written by the previous server, including integer similarity values.
        try:
            payload=json.dumps(data, sort_keys=True, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, 'Event must contain finite JSON values') from exc
        try:
            with closing(sqlite3.connect(events_path, timeout=5)) as connection:
                with connection:
                    connection.execute('INSERT OR IGNORE INTO events(event_id,payload) VALUES (?,?)', (data['event_id'],payload))
                    existing=connection.execute('SELECT payload FROM events WHERE event_id=?', (data['event_id'],)).fetchone()
                    if existing[0]!=payload:
                        raise HTTPException(409,'Event ID conflict')
        except sqlite3.Error as exc:
            raise HTTPException(503,'Attendance storage unavailable') from exc
        return {'ack':data['event_id']}

    @app.post('/v1/events', dependencies=secure, response_model=EventAck, tags=['Attendance'])
    async def attendance(data: AttendanceEvent, request: Request):
        return await run_in_threadpool(store_event, await request.json())

    return app


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host',default='0.0.0.0')
    p.add_argument('--port',type=int,default=8766)
    p.add_argument('--database',default=str(Path(__file__).with_name('faces.json')))
    p.add_argument('--events',default=str(Path(__file__).with_name('attendance.sqlite3')))
    p.add_argument('--config',default=str(Path(__file__).with_name('edge_config.json')))
    args=p.parse_args()
    config=json.loads(Path(args.config).read_text())
    app=create_app(args.database,args.events,config['api_token'],config.get('threshold',.5),config.get('match_margin',.05))
    uvicorn.run(app,host=args.host,port=args.port)


if __name__=='__main__':
    main()
