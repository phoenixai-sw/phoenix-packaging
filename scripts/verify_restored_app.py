"""Reopen an isolated recovered app through authenticated local HTTP routes.

An explicit email selector chooses an existing account in the isolated copy.
An offline test session is injected only into this local TestClient: this checks
recovered application data, not real Google authentication. No source database,
payment, AI provider or external HTTP request is used.
"""
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import sys
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from fastapi import Response
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import select

from services.api.config import Settings
from services.api.models import Asset, Job, Project, Revision, User
from services.api.auth import COOKIE_NAME, create_session
from services.api.billing.models import LedgerEntry


def require(condition,message):
    if not condition: raise ValueError(message)


@contextmanager
def isolated_runtime(directory):
    local={"APP_ENV":"test","DATABASE_URL":f"sqlite:///{(directory/'restored.db').as_posix()}",
           "STORAGE_BACKEND":"local","STORAGE_DIR":str(directory/"storage"),
           "PAYMENT_PROVIDER":"disabled","AI_PROVIDER":"disabled","DEMO_MODE":"false",
           "ENABLE_PRODUCTION_EXPORT":"false","OPERATING_POLICY_APPROVED":"false",
           "SMTP_HOST":"","MAIL_OUTBOX_DIR":str(directory/"mail"),
           "COOKIE_SECURE":"false","ALLOWED_ORIGINS":"http://testserver"}
    def deny_network(*args,**kwargs): raise RuntimeError("External HTTP is disabled during local recovery verification")
    async def deny_async_network(*args,**kwargs): raise RuntimeError("External HTTP is disabled during local recovery verification")
    # Clear inherited cloud keys before main.py creates its module-level app.
    with patch.dict(os.environ,local,clear=True),patch("httpx.HTTPTransport.handle_request",deny_network),patch("httpx.AsyncHTTPTransport.handle_async_request",deny_async_network):
        yield Settings()


def _data(response,label):
    require(response.status_code==200,f"Recovered {label} route failed")
    return response.json()["data"]


def _check_asset_content(raw,item):
    expected=(item['width_px'],item['height_px'])
    if item.get('content_type')=='image/svg+xml':
        from services.api.svg_import import sanitize_svg
        require(item.get('source')=='sanitized_svg',"Recovered SVG source type differs")
        source=sanitize_svg(raw)
        require(source.raw==raw and (source.width,source.height)==expected,"Recovered sanitized SVG or dimensions differ")
        return
    with Image.open(BytesIO(raw)) as image:
        require(image.size==expected and image.width*image.height<=40_000_000,"Recovered image dimensions differ")
        image.verify()
    with Image.open(BytesIO(raw)) as image:image.load()


def _check_export(raw,kind,*,result=None,snapshot=None,project_id=None,revision_id=None):
    """Validate existing artifacts without rebuilding them or expanding rights.

    No archive extraction. Limit compressed and expanded bytes before reads so
    an otherwise well-formed backup cannot make this verifier unpack a bomb.
    """
    result=result or {};snapshot=snapshot or {}
    require(0<len(raw)<=256*1024*1024,"Recovered export exceeds supported size")
    engine_test=result.get("format") in ("print_engine_zip","print_request_zip")
    print_request=result.get("format")=="print_request_zip"
    if kind=="review_export" and not engine_test:
        require(len(PdfReader(BytesIO(raw)).pages)>0,"Recovered review PDF has no pages")
        return
    require(kind in {"production_export","editable_export"} or engine_test,"Unsupported recovered export kind")
    with ZipFile(BytesIO(raw)) as archive:
        infos=archive.infolist();names=[info.filename for info in infos]
        require(0<len(infos)<=4096 and len(set(names))==len(names),"Recovered ZIP has duplicate or excessive members")
        require(sum(info.file_size for info in infos)<=256*1024*1024,"Recovered ZIP expanded size exceeds limit")
        for info in infos:
            path=PurePosixPath(info.filename)
            require(not path.is_absolute() and '\\' not in info.filename and ':' not in info.filename and '..' not in path.parts
                    and not info.is_dir() and not info.flag_bits&1 and (info.external_attr>>16)&0o170000!=0o120000,
                    "Recovered ZIP contains an unsafe member")
        require('manifest.json' in names and archive.getinfo('manifest.json').file_size<=32*1024*1024,"Recovered ZIP manifest missing or oversized")
        manifest=json.loads(archive.read('manifest.json'))
        entries=manifest.get('files');require(isinstance(entries,list),"Recovered ZIP file manifest missing")
        paths=[entry.get('path') if kind=='editable_export' else entry.get('name') for entry in entries]
        require(len(set(paths))==len(paths) and set(paths)==set(names)-{'manifest.json'},"Recovered ZIP file manifest differs")
        for entry,path in zip(entries,paths):
            expected_size=entry.get('byte_size') if kind=='editable_export' else entry.get('bytes')
            require(type(expected_size) is int and archive.getinfo(path).file_size==expected_size,"Recovered ZIP member size differs")
            require(sha256(archive.read(path)).hexdigest()==entry.get('sha256'),"Recovered ZIP member hash differs")
        require(archive.testzip() is None,"Recovered ZIP CRC mismatch")
        if result.get('manifest'):require(result['manifest']==manifest,"Recovered stored manifest differs")
        if project_id is not None and not engine_test:require(manifest.get('project_id')==project_id,"Recovered ZIP project differs")
        if revision_id is not None and not engine_test:require(manifest.get('revision_id')==revision_id,"Recovered ZIP revision differs")
        if kind=='editable_export':
            expected={'project.json','scene.json','geometry.json','assets.json','README.ko.txt','fonts/OFL.txt','fonts/README.md','manifest.json'}
            require(expected<=set(names) and manifest.get('format')=='phoenix-editable' and manifest.get('review_only') is True
                    and manifest.get('production_approved') is False and bool(manifest.get('rights_notice')),"Recovered editable manifest invalid")
            for name,key in [('scene.json','scene'),('geometry.json','geometry'),('structure.json','structure_snapshot')]:
                if key in snapshot and snapshot[key] is not None:
                    require(name in names and json.loads(archive.read(name))==snapshot[key],"Recovered editable snapshot differs")
            items=json.loads(archive.read('assets.json'))['items']
            require(len({item['id'] for item in items})==len(items),"Recovered asset index contains duplicates")
            for item in items:
                content=archive.read(item['path'])
                require(len(content)==item['byte_size'] and sha256(content).hexdigest()==item['sha256'],"Recovered indexed asset differs")
                _check_asset_content(content,item)
            for font in manifest.get('fonts',[]):
                require(font['path'] in names and sha256(archive.read(font['path'])).hexdigest()==font['sha256'],"Recovered font hash differs")
                require(font.get('license') and (font.get('license_path') or 'fonts/OFL.txt') in names,"Recovered font license missing")
            return
        required={'production.pdf','preview.png','preflight.json','manifest.json'}
        if kind=='production_export':required|={'job-ticket.json','job-ticket.pdf'}
        if engine_test or manifest.get('adapter')=='icc-cmyk-outline-v1':
            required|={'cut.pdf','fold.pdf'}
            # Bundles rendered before the layered combined file existed remain valid.
            if 'artwork-with-dieline.pdf' in names:required|={'artwork-with-dieline.pdf'}
            if 'process.pdf' in names:required|={'process.pdf','finishing.json'}
        require(set(names)==required,"Recovered print bundle members differ")
        require(manifest.get('kind')==('print_request' if print_request else 'print_engine_test' if engine_test else 'production'),"Recovered print purpose differs")
        if engine_test:require(manifest.get('manufacturer_approval') is False,"Recovered test/request output must stay unapproved")
        if engine_test and not print_request:require(manifest.get('review_only') is True,"Recovered test output must stay review-only")
        for name in names:
            if name.endswith('.pdf'):require(len(PdfReader(BytesIO(archive.read(name))).pages)>0,"Recovered print PDF has no pages")
            elif name.endswith('.json'):json.loads(archive.read(name))
        with Image.open(BytesIO(archive.read('preview.png'))) as image:
            require(image.width*image.height<=40_000_000,"Recovered print preview exceeds pixel limit")
            image.load()


def verify_restored_app(restore_dir,credentials_path):
    directory=Path(restore_dir).resolve()
    require((directory/"restored.db").is_file() and (directory/"storage").is_dir(),"A completed isolated restore is required")
    require(json.loads((directory/"verification.json").read_text(encoding="utf-8")).get("verified") is True,"Logical recovery verification must pass before reopening the app")
    credentials=json.loads(Path(credentials_path).read_text(encoding="utf-8"))
    require(isinstance(credentials,dict) and isinstance(credentials.get("email"),str),"QA selector must contain an existing account email")
    with isolated_runtime(directory) as settings:
        from services.api.main import create_app
        app=create_app(settings)
        with TestClient(app,follow_redirects=False) as client:
            # The operator already decrypted this isolated backup. Never deploy
            # this identity fixture as an HTTP endpoint or hosted auth fallback.
            with app.state.session_factory() as db:
                user=db.scalar(select(User).where(User.email==credentials['email'].strip().lower()))
                require(user is not None and user.is_active,"Recovered account selector is not an active existing user")
                legacy_identity=not bool(user.google_sub)
                if legacy_identity:
                    user.google_sub='offline-recovery-'+user.id
                response=Response()
                session=create_session(db,user,response,settings)
                db.commit()
                client.headers['X-CSRF-Token']=session.csrf_token
                from http.cookies import SimpleCookie
                cookie=SimpleCookie();cookie.load(response.headers['set-cookie'])
                client.cookies.set(COOKIE_NAME,cookie[COOKIE_NAME].value)
            login=_data(client.get('/v1/me'),"session")
            require(login["user"]["role"]=="owner","An existing QA owner account is required")
            tenant=login["tenant"]["id"]
            with app.state.session_factory() as db:
                projects=list(db.scalars(select(Project).where(Project.tenant_id==tenant)))
                assets=list(db.scalars(select(Asset).where(Asset.tenant_id==tenant)))
                jobs=list(db.scalars(select(Job).where(Job.tenant_id==tenant)))
                ledger=[{column.name:getattr(row,column.name) for column in LedgerEntry.__table__.columns} for row in db.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id==tenant))]
                revisions={p.id:list(db.scalars(select(Revision).where(Revision.project_id==p.id,Revision.tenant_id==tenant).order_by(Revision.number.desc()).limit(100))) for p in projects}
            asset_ids={asset.id for asset in assets}
            job_ids={job.id for job in jobs}
            linked_images=0
            reopened_revisions=0
            for item in projects:
                reopened=_data(client.get(f"/v1/projects/{item.id}"),"scene")
                require(reopened["scene"]==item.scene and reopened["base_revision"]==item.base_revision,"Recovered scene differs")
                for face in item.scene["faces"]:
                    for obj in face["objects"]:
                        if obj["type"]=="image":
                            require(obj["asset_id"] in asset_ids,"Recovered scene asset link is broken")
                            linked_images+=1
                previous=_data(client.get(f"/v1/projects/{item.id}/revisions"),"revision")["items"]
                require({r["id"]:r["scene"] for r in previous}=={r.id:r.scene for r in revisions[item.id]},"Recovered revisions differ")
                reopened_revisions+=len(previous)
            credit_data=_data(client.get("/v1/credits"),"ledger")
            # Read-time expiry may append new ledger entries locally; original
            # source entries must remain byte-for-value unchanged.
            with app.state.session_factory() as db:
                after={row.id:row for row in db.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id==tenant))}
                for entry in ledger:
                    require(entry["id"] in after,"Recovered ledger entry disappeared")
                    require(all(getattr(after[entry["id"]],key)==value for key,value in entry.items()),"Recovered ledger entry changed")
                    if entry["job_id"]: require(entry["job_id"] in job_ids,"Recovered ledger job link is broken")
                for entry in credit_data["ledger"]:
                    require(entry["id"] in after and after[entry["id"]].amount==entry["amount"] and after[entry["id"]].event==entry["event"],"Recovered ledger API differs")
            reopened_assets=0
            unavailable_assets=0
            for asset in assets:
                response=client.get(f"/v1/assets/{asset.id}/content")
                if asset.metadata_json.get("integrity_status")=="unavailable_compensated" or asset.metadata_json.get("_retention", {}).get("state") in {"deleting", "deleted"}:
                    require(response.status_code==410,"Recovered compensated asset must remain unavailable")
                    unavailable_assets+=1
                    continue
                require(response.status_code==200,"Recovered asset route failed")
                stored=app.state.storage.get(asset.storage_key)
                require(len(response.content)==asset.byte_size and response.content==stored,"Recovered asset bytes differ")
                if asset.metadata_json.get("sha256"):
                    require(sha256(stored).hexdigest()==asset.metadata_json["sha256"],"Recovered asset baseline differs")
                if asset.content_type=='image/svg+xml':
                    require(sha256(stored).hexdigest()==asset.metadata_json.get('sha256'),"Recovered SVG baseline missing or changed")
                    require(response.headers.get('content-type')=='application/octet-stream'
                            and response.headers.get('content-disposition','').startswith('attachment;')
                            and response.headers.get('x-content-type-options')=='nosniff',"Recovered SVG must remain an inert attachment")
                _check_asset_content(stored,{'content_type':asset.content_type,'source':asset.source,
                                            'width_px':asset.width_px,'height_px':asset.height_px})
                reopened_assets+=1
            reopened_exports=0;unavailable_exports=0
            for job in jobs:
                item=_data(client.get(f"/v1/jobs/{job.id}"),"job")
                require(item["project_id"]==job.project_id and item["status"]==job.status,"Recovered job link differs")
                if job.kind.endswith('_export') and (job.result or {}).get('_integrity',{}).get('state')=='unavailable':
                    response=client.get(f'/v1/exports/{job.id}/download')
                    require(response.status_code==410 and item['download_url'] is None,'Recovered unavailable export must remain unavailable')
                    unavailable_exports+=1
                    continue
                if job.status=="succeeded" and job.kind.endswith("_export") and job.result and job.result.get("storage_key"):
                    response=client.get(f"/v1/exports/{job.id}/download")
                    if job.result.get("_retention", {}).get("state") in {"deleting", "deleted"}:
                        require(response.status_code==410 and item["download_url"] is None,"Recovered deleted export must remain unavailable")
                        continue
                    require(response.status_code==200,"Recovered export download failed")
                    raw=response.content
                    require(raw==app.state.storage.get(job.result["storage_key"]),"Recovered export bytes differ")
                    if job.result.get("sha256"): require(sha256(raw).hexdigest()==job.result["sha256"],"Recovered export baseline differs")
                    _check_export(raw,job.kind,result=job.result,snapshot=job.snapshot,project_id=job.project_id,revision_id=job.revision_id)
                    reopened_exports+=1
            report={"application_reopen_verified":True,"transport":"local FastAPI TestClient",
                    "external_network_enabled":False,"source_database_used":False,
                    "authenticated_existing_qa_owner":False,"offline_existing_owner_session":True,
                    "google_authentication_tested":False,"legacy_identity_fixture":legacy_identity,"projects_reopened":len(projects),
                    "revisions_reopened":reopened_revisions,"scene_asset_links_verified":linked_images,
                    "assets_reopened":reopened_assets,"known_unavailable_assets":unavailable_assets,
                      "jobs_reopened":len(jobs),"export_files_reopened":reopened_exports,"known_unavailable_exports":unavailable_exports,
                    "ledger_entries_preserved":len(ledger),"ledger_api_entries_reopened":len(credit_data["ledger"]),
                    "ac31_scenario_covered":bool(projects and ledger and reopened_exports and reopened_assets),
                    "postgres_physical_restore_tested":False,
                      "local_application_mutations":["offline_test_session","credit_expiry_refresh","export_integrity_observation"]+(["legacy_identity_fixture"] if legacy_identity else [])}
    (directory/"application-verification.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    return report
