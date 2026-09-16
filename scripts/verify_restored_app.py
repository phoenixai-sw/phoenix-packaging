"""Reopen an isolated recovered app through authenticated local HTTP routes.

Only an explicitly supplied email/password JSON file is read. No source database,
SMTP, payment, AI provider or external HTTP request is used. The restored copy may
create local login sessions and refresh expired credits just like a normal app.
"""
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import select

from services.api.config import Settings
from services.api.models import Asset, Job, Project, Revision
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


def _check_export(raw,kind):
    if kind=="production_export":
        with ZipFile(BytesIO(raw)) as archive:
            expected={"production.pdf","preview.png","job-ticket.json","job-ticket.pdf","preflight.json","manifest.json"}
            require(set(archive.namelist())==expected and archive.testzip() is None,"Recovered production bundle is invalid")
            for name in ("production.pdf","job-ticket.pdf"):
                require(len(PdfReader(BytesIO(archive.read(name))).pages)>0,"Recovered production PDF has no pages")
            with Image.open(BytesIO(archive.read("preview.png"))) as image: image.load()
    else:
        require(len(PdfReader(BytesIO(raw)).pages)>0,"Recovered review PDF has no pages")


def verify_restored_app(restore_dir,credentials_path):
    directory=Path(restore_dir).resolve()
    require((directory/"restored.db").is_file() and (directory/"storage").is_dir(),"A completed isolated restore is required")
    require(json.loads((directory/"verification.json").read_text(encoding="utf-8")).get("verified") is True,"Logical recovery verification must pass before reopening the app")
    credentials=json.loads(Path(credentials_path).read_text(encoding="utf-8"))
    require(isinstance(credentials,dict) and isinstance(credentials.get("email"),str) and isinstance(credentials.get("password"),str),"QA credentials must contain email and password")
    with isolated_runtime(directory) as settings:
        from services.api.main import create_app
        app=create_app(settings)
        with TestClient(app,follow_redirects=False) as client:
            login=_data(client.post("/v1/auth/login",json={"email":credentials["email"],"password":credentials["password"]}),"login")
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
                if asset.metadata_json.get("integrity_status")=="unavailable_compensated":
                    require(response.status_code==410,"Recovered compensated asset must remain unavailable")
                    unavailable_assets+=1
                    continue
                require(response.status_code==200,"Recovered asset route failed")
                stored=app.state.storage.get(asset.storage_key)
                require(len(response.content)==asset.byte_size and response.content==stored,"Recovered asset bytes differ")
                if asset.metadata_json.get("sha256"):
                    require(sha256(stored).hexdigest()==asset.metadata_json["sha256"],"Recovered asset baseline differs")
                with Image.open(BytesIO(stored)) as image:
                    image.load()
                    require(image.size==(asset.width_px,asset.height_px),"Recovered image dimensions differ")
                reopened_assets+=1
            reopened_exports=0
            for job in jobs:
                item=_data(client.get(f"/v1/jobs/{job.id}"),"job")
                require(item["project_id"]==job.project_id and item["status"]==job.status,"Recovered job link differs")
                if job.status=="succeeded" and job.kind.endswith("_export") and job.result and job.result.get("storage_key"):
                    response=client.get(f"/v1/exports/{job.id}/download")
                    require(response.status_code==200,"Recovered export download failed")
                    raw=response.content
                    require(raw==app.state.storage.get(job.result["storage_key"]),"Recovered export bytes differ")
                    if job.result.get("sha256"): require(sha256(raw).hexdigest()==job.result["sha256"],"Recovered export baseline differs")
                    _check_export(raw,job.kind)
                    reopened_exports+=1
            report={"application_reopen_verified":True,"transport":"local FastAPI TestClient",
                    "external_network_enabled":False,"source_database_used":False,
                    "authenticated_existing_qa_owner":True,"projects_reopened":len(projects),
                    "revisions_reopened":reopened_revisions,"scene_asset_links_verified":linked_images,
                    "assets_reopened":reopened_assets,"known_unavailable_assets":unavailable_assets,
                    "jobs_reopened":len(jobs),"export_files_reopened":reopened_exports,
                    "ledger_entries_preserved":len(ledger),"ledger_api_entries_reopened":len(credit_data["ledger"]),
                    "ac31_scenario_covered":bool(projects and ledger and reopened_exports and reopened_assets),
                    "postgres_physical_restore_tested":False,
                    "local_application_mutations":["login_session","credit_expiry_refresh"]}
    (directory/"application-verification.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    return report
