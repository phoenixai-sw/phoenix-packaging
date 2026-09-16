"""Apply prepared server settings to the linked Vercel API, without secret output."""
import argparse,json,subprocess,sys
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]
LOCAL=ROOT/".local"
parser=argparse.ArgumentParser();parser.add_argument("--keys",nargs="*");parser.add_argument("--storage",action="store_true");args=parser.parse_args()
env=json.loads((LOCAL/"cloud-env.json").read_text(encoding="utf-8-sig"))
target=json.loads((LOCAL/"vercel-api-created.json").read_text(encoding="utf-8-sig"))
try:
    if args.storage:
        headers={"Authorization":"Bearer "+env["SUPABASE_SERVICE_ROLE_KEY"],"apikey":env["SUPABASE_SERVICE_ROLE_KEY"]}
        with httpx.Client(timeout=30) as client:
            for bucket,limit,mimes in (("phoenix-private",200*1024*1024,["image/png","image/jpeg","image/webp","application/pdf","application/json","application/zip"]),("phoenix-private-uploads",20*1024*1024,["image/png","image/jpeg","image/webp"])):
                data={"id":bucket,"name":bucket,"public":False,"file_size_limit":limit,"allowed_mime_types":mimes}
                url=env["SUPABASE_URL"]+"/storage/v1/bucket"
                exists=client.get(url+"/"+bucket,headers=headers)
                response=client.put(url+"/"+bucket,headers=headers,json=data) if exists.status_code==200 else client.post(url,headers=headers,json=data)
                response.raise_for_status()
                check=client.get(url+"/"+bucket,headers=headers);check.raise_for_status()
                assert check.json()["public"] is False
            print("Private asset and quarantine buckets configured.")
    names=args.keys or list(env)
    for name in names:
        payload={"key":name,"value":env[name],"type":"encrypted","target":["production","preview"]}
        path=LOCAL/"platform-env-input.json";path.write_text(json.dumps(payload),encoding="utf-8")
        response=subprocess.run(["vercel.cmd","api",f"/v10/projects/{target['id']}/env?upsert=true","-X","POST","--scope","phoenixs-projects-ea6a0b8e","--input",str(path)],cwd=ROOT,capture_output=True,text=True)
        if response.returncode: raise RuntimeError("Vercel setting update failed for "+name)
        print("Configured "+name+" (encrypted).",flush=True)
except Exception as exc:
    print("Cloud configuration failed: "+type(exc).__name__)
    if isinstance(exc,httpx.HTTPStatusError):
        try:
            info=exc.response.json()
            print(json.dumps({k:info[k] for k in ("statusCode","error","message") if k in info})[:700])
        except Exception: print("HTTP status "+str(exc.response.status_code))
    sys.exit(1)
