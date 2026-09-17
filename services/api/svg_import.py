"""Bounded, static SVG import; only the raster is a placeable design asset.

Untrusted XML never reaches a browser or renderer. A fresh allowlisted tree is
rendered in a short-lived process without host fonts. The sanitized vector is
kept as a private source Asset, so quota, backup and retention see real bytes.
"""
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from math import ceil, cos, isfinite, radians, sin, tan
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4
from xml.etree import ElementTree as ET

from defusedxml.ElementTree import fromstring
import resvg_py
from PIL import Image, ImageColor
from sqlalchemy import func, select, update

from .errors import APIError
from .models import Asset, Tenant
from .retention.deletion import available_asset_clause

SVG_MIME = "image/svg+xml"
SVG_SOURCE = "sanitized_svg"
VERSION = "static-svg-import-v1"
MAX_SVG_BYTES = 1024 * 1024
MAX_NODES = 2000
MAX_DEPTH = 24
MAX_NUMBERS = 50000
MAX_PATH_COMMANDS = 10000
MAX_PIXELS = 16_000_000
MAX_EDGE = 8192
RENDER_TIMEOUT = 8
MAX_PNG_BYTES = 20 * 1024 * 1024
QUOTA_BYTES = 200 * 1024 * 1024
NS = "http://www.w3.org/2000/svg"
NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
NUM_RE = re.compile(NUMBER)
ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,79}\Z")
LOCAL_URL = re.compile(r"url\(#([A-Za-z_][A-Za-z0-9_.-]{0,79})\)\Z")
DRAW = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon"}
ELEMENTS = DRAW | {"svg", "g", "defs", "linearGradient", "radialGradient", "stop", "clipPath"}
NUMERIC = {"x", "y", "width", "height", "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry", "fx", "fy", "fr", "offset", "stroke-width", "stroke-miterlimit", "stroke-dashoffset"}
OPACITY = {"opacity", "fill-opacity", "stroke-opacity", "stop-opacity"}
PAINT = {"fill", "stroke", "color", "stop-color"}
ENUMS = {"fill-rule": {"nonzero", "evenodd"}, "clip-rule": {"nonzero", "evenodd"},
         "stroke-linecap": {"butt", "round", "square"}, "stroke-linejoin": {"miter", "round", "bevel"},
         "gradientUnits": {"objectBoundingBox", "userSpaceOnUse"}, "clipPathUnits": {"objectBoundingBox", "userSpaceOnUse"},
         "spreadMethod": {"pad", "reflect", "repeat"}, "display": {"none", "inline"},
         "visibility": {"visible", "hidden"}, "vector-effect": {"none", "non-scaling-stroke"}}
STYLE = PAINT | OPACITY | {"fill-rule", "clip-rule", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset", "display", "visibility"}
IDENTITY = (1., 0., 0., 1., 0., 0.)


def fail(code="SVG_UNSUPPORTED", message="이 SVG에는 지원하지 않는 기능이 있습니다. 정적 도형과 윤곽선으로 정리해 다시 올려 주세요."):
    raise APIError(422, code, message)


def numbers(value, *, count=None, limit=1_000_000):
    tokens = NUM_RE.findall(value)
    rest = NUM_RE.sub("", value)
    if not tokens or re.sub(r"[\s,]", "", rest) or count is not None and len(tokens) != count:
        fail("SVG_INVALID", "SVG의 좌표 또는 치수를 확인해 주세요.")
    result = [float(x) for x in tokens]
    if any(not isfinite(x) or abs(x) > limit for x in result):
        fail("SVG_COMPLEXITY_LIMIT", "SVG 좌표 범위가 너무 큽니다.")
    return result


def multiply(a, b):
    x = (a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1], a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3], a[0]*b[4]+a[2]*b[5]+a[4], a[1]*b[4]+a[3]*b[5]+a[5])
    if any(not isfinite(v) or abs(v)>1_000_000 for v in x):
        fail("SVG_COMPLEXITY_LIMIT", "SVG의 중첩 변환 범위가 너무 큽니다.")
    return x


def transform(value):
    result = IDENTITY
    pattern = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^()]*)\)")
    matches = list(pattern.finditer(value))
    if not matches or len(matches)>16 or re.sub(r"[\s,]", "", pattern.sub("", value)):
        fail("SVG_INVALID", "SVG 변환식을 확인해 주세요.")
    for match in matches:
        name, args = match.groups(); v = numbers(args)
        if name == "matrix" and len(v)==6: m=tuple(v)
        elif name == "translate" and len(v) in {1,2}: m=(1,0,0,1,v[0],v[1] if len(v)==2 else 0)
        elif name == "scale" and len(v) in {1,2}: m=(v[0],0,0,v[-1],0,0)
        elif name == "rotate" and len(v) in {1,3}:
            a=radians(v[0]); m=(cos(a),sin(a),-sin(a),cos(a),0,0)
            if len(v)==3: m=multiply(multiply((1,0,0,1,v[1],v[2]),m),(1,0,0,1,-v[1],-v[2]))
        elif name in {"skewX","skewY"} and len(v)==1 and abs(v[0])<89:
            t=tan(radians(v[0])); m=(1,0,t,1,0,0) if name=="skewX" else (1,t,0,1,0,0)
        else: fail("SVG_INVALID", "SVG 변환식을 확인해 주세요.")
        result=multiply(result,m)
    return result


def length(value):
    match = re.fullmatch(r"\s*("+NUMBER+r")\s*(px|mm|cm|in|pt|pc)?\s*", value)
    if not match: fail("SVG_DIMENSIONS_REQUIRED", "SVG 폭·높이는 px, mm, cm, in, pt 또는 viewBox로 지정해 주세요.")
    n=float(match[1])*{None:1,"px":1,"mm":96/25.4,"cm":96/2.54,"in":96,"pt":96/72,"pc":16}[match[2]]
    if not isfinite(n) or not 1<=n<=MAX_EDGE: fail("SVG_SIZE_LIMIT", "SVG 한 변은 1~8192px 범위여야 합니다.")
    return ceil(n)


@dataclass(frozen=True)
class SanitizedSVG:
    raw: bytes
    width: int
    height: int
    removed: tuple[str, ...]


def sanitize_svg(raw: bytes) -> SanitizedSVG:
    if not raw or len(raw)>MAX_SVG_BYTES: fail("SVG_SIZE_LIMIT", "SVG 원본은 1MiB 이하로 올려 주세요.")
    try:
        text=raw.decode("utf-8-sig")
        root=fromstring(text, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except Exception:
        fail("SVG_INVALID", "UTF-8 SVG만 지원하며 DTD·엔티티·압축 SVG는 허용하지 않습니다.")
    if root.tag not in {"svg",f"{{{NS}}}svg"}: fail("SVG_INVALID", "SVG 루트 요소를 확인해 주세요.")
    view=numbers(root.attrib["viewBox"],count=4) if "viewBox" in root.attrib else None
    if view and (view[2]<.01 or view[3]<.01): fail("SVG_INVALID", "SVG viewBox 크기는 양수여야 합니다.")
    width=length(root.attrib.get("width",str(view[2]) if view else ""))
    height=length(root.attrib.get("height",str(view[3]) if view else ""))
    if width*height>MAX_PIXELS: fail("SVG_SIZE_LIMIT", "SVG 래스터 변환은 1,600만 픽셀 이하를 지원합니다.")
    counts={"nodes":0,"numbers":0,"commands":0}; ids={}; refs=[]; removed=set()

    def visit(node, depth=0, matrix=IDENTITY, in_clip=False):
        counts["nodes"]+=1
        if counts["nodes"]>MAX_NODES or depth>MAX_DEPTH: fail("SVG_COMPLEXITY_LIMIT", "SVG 요소 수 또는 중첩 깊이가 한도를 초과했습니다.")
        if width*height*(depth+1)>64_000_000:
            fail("SVG_COMPLEXITY_LIMIT", "큰 SVG의 중첩 처리 메모리 한도를 초과했습니다.")
        tag=node.tag
        if tag.startswith("{"):
            namespace,tag=tag[1:].split("}",1)
            if namespace!=NS:
                if depth and namespace in {"http://www.inkscape.org/namespaces/inkscape","http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd","http://www.w3.org/1999/02/22-rdf-syntax-ns#"}:
                    removed.add("editor_metadata"); return None
                fail()
        if tag in {"title","desc","metadata"}: removed.add("metadata"); return None
        if tag in {"text","tspan","textPath","font","glyph"}: fail("SVG_TEXT_OUTLINE_REQUIRED", "SVG 글자는 원래 글꼴로 윤곽선 변환 후 올려 주세요. 편집 가능한 문구는 편집기의 텍스트 도구로 추가할 수 있습니다.")
        if tag not in ELEMENTS or depth and tag=="svg": fail()
        if (node.text or "").strip() or (node.tail or "").strip(): fail("SVG_INVALID", "SVG 도형 밖의 텍스트를 확인해 주세요.")
        if in_clip and tag not in DRAW | {"g"}: fail()
        attrs={}; style_attrs={}
        for key,value in node.attrib.items():
            if key.startswith("{"):
                if key.endswith("}href") or key.endswith("}base"): fail()
                removed.add("editor_attributes"); continue
            if key.lower().startswith("on"): fail("SVG_ACTIVE_CONTENT", "이벤트·스크립트가 포함된 SVG는 가져올 수 없습니다.")
            if key=="style":
                for pair in value.split(";"):
                    if not pair.strip(): continue
                    if ":" not in pair: fail()
                    prop,val=map(str.strip,pair.split(":",1))
                    if prop not in STYLE: fail()
                    style_attrs[prop]=val
            elif key in {"version","class"} or key.startswith("data-"): removed.add("nonvisual_attributes")
            else: attrs[key]=value.strip()
        # Inline style wins over presentation attributes, independently of XML
        # attribute order. Flatten only the supported declaration vocabulary.
        attrs.update(style_attrs)
        out=ET.Element(tag)
        for key,value in attrs.items():
            if len(value)>MAX_SVG_BYTES: fail()
            counts["numbers"]+=len(NUM_RE.findall(value))
            if counts["numbers"]>MAX_NUMBERS: fail("SVG_COMPLEXITY_LIMIT", "SVG 좌표 수가 한도를 초과했습니다.")
            if key=="id":
                if not ID_RE.fullmatch(value) or value in ids: fail("SVG_INVALID", "SVG 내부 식별자가 중복되거나 올바르지 않습니다.")
                ids[value]=tag
            elif key in {"transform","gradientTransform"}:
                current=transform(value)
                if key=="transform": matrix=multiply(matrix,current)
            elif key=="viewBox":
                if depth or tag!="svg": fail()
                numbers(value,count=4)
            elif key=="preserveAspectRatio":
                if not re.fullmatch(r"(?:none|x(?:Min|Mid|Max)Y(?:Min|Mid|Max)(?:\s+(?:meet|slice))?)",value): fail()
            elif key in {"width","height"} and not depth:
                value=str(width if key=="width" else height)
            elif key in NUMERIC:
                n=numbers(value[:-1] if value.endswith("%") else value,count=1)[0]
                if key in {"width","height","r","rx","ry","fr","stroke-width","stroke-miterlimit"} and n<0: fail()
            elif key in OPACITY:
                if not 0<=numbers(value,count=1)[0]<=1: fail()
            elif key in PAINT:
                ref=LOCAL_URL.fullmatch(value)
                if ref: refs.append((ref[1],"gradient"))
                elif value not in {"none","currentColor","transparent"}:
                    try:
                        if not re.fullmatch(r"(?:#[0-9a-fA-F]{3}|#[0-9a-fA-F]{4}|#[0-9a-fA-F]{6}|#[0-9a-fA-F]{8}|[a-zA-Z]{1,32}|rgba?\([0-9.,%\s]+\))",value): raise ValueError()
                        ImageColor.getrgb(value)
                    except ValueError: fail("SVG_INVALID", "SVG 색상 값을 확인해 주세요.")
            elif key=="clip-path":
                if in_clip or tag=="clipPath": fail()
                ref=LOCAL_URL.fullmatch(value)
                if not ref: fail()
                refs.append((ref[1],"clipPath"))
            elif key in ENUMS:
                if value not in ENUMS[key]: fail()
            elif key=="stroke-dasharray":
                if value!="none":
                    vals=numbers(value)
                    if len(vals)>64 or any(x<0 for x in vals): fail()
            elif key=="points":
                vals=numbers(value)
                if len(vals)%2: fail()
            elif key=="d":
                chars=re.sub(NUMBER,"",value)
                if re.sub(r"[MmZzLlHhVvCcSsQqTtAa\s,]","",chars): fail()
                vals=NUM_RE.findall(value)
                if any(not isfinite(float(x)) or abs(float(x))>1_000_000 for x in vals): fail()
                counts["commands"]+=len(re.findall(r"[MmZzLlHhVvCcSsQqTtAa]",chars))
                if counts["commands"]>MAX_PATH_COMMANDS: fail("SVG_COMPLEXITY_LIMIT", "SVG 경로 명령 수가 한도를 초과했습니다.")
            else: fail()
            out.set(key,value)
        for child in node:
            clean=visit(child,depth+1,matrix,in_clip or tag=="clipPath")
            if clean is not None: out.append(clean)
        return out

    clean=visit(root)
    for identity,kind in refs:
        if ids.get(identity) not in ({"linearGradient","radialGradient"} if kind=="gradient" else {kind}): fail("SVG_INVALID", "SVG 내부 참조를 확인해 주세요.")
    clean.set("width",str(width));clean.set("height",str(height));clean.set("xmlns",NS)
    # Canonical order must remain identical when the private source is checked
    # again by ZIP and backup restore (including viewBox-only SVG inputs).
    for node in clean.iter():
        attrs=sorted(node.attrib.items());node.attrib.clear();node.attrib.update(attrs)
    return SanitizedSVG(ET.tostring(clean,encoding="utf-8"),width,height,tuple(sorted(removed)))


# No file path, network URL, font path or external stylesheet enters this code.
_RENDER_CODE = """import sys
if sys.platform == 'linux':
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (512*1024*1024, 512*1024*1024))
sys.path.insert(0,sys.argv[1])
import resvg_py
sys.stdout.buffer.write(resvg_py.svg_to_bytes(svg_string=sys.stdin.buffer.read().decode('utf-8'),skip_system_fonts=True,log_information=False,dpi=96))
"""


def rasterize_svg(source: SanitizedSVG) -> bytes:
    env={k:v for k,v in os.environ.items() if k.upper() in {"SYSTEMROOT","WINDIR","PATH","TEMP","TMP"}}
    try:
        # A serverless parent can load vendored packages outside the interpreter's
        # default site-packages. Pass only the already imported package location;
        # never inherit client-controlled PYTHONPATH or a source SVG file path.
        package_root=str(Path(resvg_py.__file__).resolve().parent.parent)
        result=subprocess.run([sys.executable,"-I","-c",_RENDER_CODE,package_root],input=source.raw,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            timeout=RENDER_TIMEOUT,check=True,env=env,creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
        raw=result.stdout
        if len(raw)>MAX_PNG_BYTES: fail("SVG_SIZE_LIMIT", "변환된 PNG가 20MiB를 초과합니다. SVG 크기를 줄여 주세요.")
        with Image.open(BytesIO(raw)) as im:
            if im.format!="PNG" or im.size!=(source.width,source.height): raise ValueError()
            im.load()
        return raw
    except subprocess.TimeoutExpired:
        fail("SVG_RENDER_TIMEOUT", "SVG가 너무 복잡해 8초 변환 한도를 초과했습니다. 경로를 단순화해 주세요.")
    except APIError: raise
    except Exception:
        fail("SVG_RENDER_FAILED", "SVG를 안전한 이미지로 변환하지 못했습니다. 도형·경로를 확인해 주세요.")


@dataclass(frozen=True)
class PreparedImage:
    raw: bytes
    content_type: str
    width: int
    height: int
    svg: SanitizedSVG | None = None
    uploaded_sha256: str | None = None


def prepare_image_upload(raw: bytes, content_type: str) -> PreparedImage:
    if content_type==SVG_MIME:
        source=sanitize_svg(raw)
        return PreparedImage(rasterize_svg(source),"image/png",source.width,source.height,source,sha256(raw).hexdigest())
    if content_type not in {"image/png","image/jpeg","image/webp"}:
        raise APIError(422,"ASSET_TYPE_UNSUPPORTED","PNG, JPEG, WebP 또는 정적 윤곽선 SVG를 지원합니다.")
    try:
        with Image.open(BytesIO(raw)) as im:
            w,h=im.size
            if Image.MIME.get(im.format)!=content_type or not 0<w*h<=40_000_000: raise ValueError()
            im.verify()
        with Image.open(BytesIO(raw)) as im: im.load()
    except Exception:
        raise APIError(422,"ASSET_INVALID","파일 형식 또는 이미지 크기를 확인해 주세요.") from None
    return PreparedImage(raw,content_type,w,h)


def store_prepared_image(db, storage, user, project, name: str, prepared: PreparedImage) -> Asset:
    """Caller commits once. Both immutable rows count toward the tenant quota."""
    db.execute(update(Tenant).where(Tenant.id==user.tenant_id).values(name=Tenant.name).execution_options(synchronize_session=False))
    used=db.scalar(select(func.coalesce(func.sum(Asset.byte_size),0)).where(Asset.tenant_id==user.tenant_id,available_asset_clause(include_deleting=True)))
    source=prepared.svg
    if used+len(prepared.raw)+(len(source.raw) if source else 0)>QUOTA_BYTES:
        raise APIError(422,"ASSET_QUOTA_EXCEEDED","원본과 변환 이미지를 포함한 작업 공간의 200MiB 저장 한도를 초과했습니다.")
    name=Path(name or "image").name[:160]
    metadata={"sha256":sha256(prepared.raw).hexdigest()}
    if source:
        source_id=str(uuid4()); source_key=f"{user.tenant_id}/assets/{source_id}"
        provenance={"version":VERSION,"uploaded_sha256":prepared.uploaded_sha256,"sanitized_sha256":sha256(source.raw).hexdigest(),
            "source_asset_id":source_id,"width_px":source.width,"height_px":source.height,"removed":list(source.removed),
            "renderer":"resvg-py/0.5.0","render_mode":"static_vector_to_png","system_fonts":False,
            "notice":"정화한 벡터 원본과 실제 PNG 변환 기록입니다. 글꼴·AI·인쇄 승인 기록이 아닙니다."}
        storage.put(source_key,source.raw,"application/octet-stream")
        db.add(Asset(id=source_id,tenant_id=user.tenant_id,workspace_id=project.workspace_id if project else None,storage_key=source_key,
            original_name=name,content_type=SVG_MIME,byte_size=len(source.raw),width_px=source.width,height_px=source.height,source=SVG_SOURCE,
            metadata_json={"sha256":provenance["sanitized_sha256"],"svg_import":{k:v for k,v in provenance.items() if k!="source_asset_id"}}))
        metadata["svg_import"]=provenance
        name=(Path(name).stem[:145]+".png")
    identity=str(uuid4());key=f"{user.tenant_id}/assets/{identity}"
    storage.put(key,prepared.raw,prepared.content_type)
    asset=Asset(id=identity,tenant_id=user.tenant_id,workspace_id=project.workspace_id if project else None,storage_key=key,original_name=name,
        content_type=prepared.content_type,byte_size=len(prepared.raw),width_px=prepared.width,height_px=prepared.height,
        source="svg_import" if source else "upload",metadata_json=metadata)
    db.add(asset);db.flush()
    return asset
