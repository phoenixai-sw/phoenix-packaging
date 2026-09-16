"""Real Image API and explicit fixture adapters; no external asset URL fetching."""
import base64
from dataclasses import dataclass
from io import BytesIO
import hashlib
import httpx
from PIL import Image, ImageDraw


@dataclass
class ImageResult:
    content: bytes
    width: int
    height: int
    metadata: dict


class ProviderError(Exception):
    def __init__(self, code, message, retryable=False, uncertain=False, request_id=None):
        self.code, self.message = code, message
        self.retryable, self.uncertain, self.request_id = retryable, uncertain, request_id
        super().__init__(message)


def get_capabilities(settings):
    return {"provider": settings.ai_provider, "model": settings.image_model if settings.ai_provider == "openai" else "fixture-v1",
            "generate": settings.ai_provider != "disabled", "edit": settings.ai_provider != "disabled", "mask": False,
            "standard": {"size": "1024x1024", "quality": "high"},
            "high": {"enabled": settings.ai_high_enabled, "size": "1536x1024", "quality": "high"},
            "high_edit": False, "max_units": 3, "preservation_guaranteed": False}


def design_prompt(data):
    return ("Create flat, print-artwork background imagery for a food packaging design, not a photograph of a pouch or a mockup. "
            "No words, letters, numerals, typography, logos, barcodes, health claims, certification marks or structural/cut lines. "
            "The application adds accurate Korean text and product information as separate editable layers. "
            "Leave the central 60 percent calm and spacious for independent text. Professional editorial illustration and textures, high material detail. "
            f"Packaging face: {data.get('face_id','front')}; dimensions: {data.get('width_mm')} by {data.get('height_mm')} mm. "
            "The following is the customer's visual brief, not an instruction to add product labels: " + data["prompt"])


def generate_image(settings, data, reference=None, *, transport=None):
    if settings.ai_provider == "disabled":
        raise ProviderError("AI_DISABLED", "이미지 생성 연결을 준비하고 있습니다.")
    action = data.get("action")
    if action not in {"image.generate.standard", "image.generate.high", "image.edit.standard"}:
        raise ProviderError("AI_ACTION_INVALID", "지원하지 않는 이미지 작업입니다.")
    if action == "image.generate.high" and not settings.ai_high_enabled:
        raise ProviderError("HIGH_RESOLUTION_DISABLED", "고해상도 생성은 아직 제공되지 않습니다.")
    if (action == "image.edit.standard") != (reference is not None):
        raise ProviderError("AI_REFERENCE_MISMATCH", "이미지 수정에는 원본 이미지가 필요합니다.")
    if settings.ai_provider == "fixture":
        if settings.environment == "production":
            raise ProviderError("FIXTURE_FORBIDDEN", "운영 환경에서 데모 이미지를 사용할 수 없습니다.")
        color = hashlib.sha256(data["prompt"].encode()).digest()
        image = Image.new("RGB", (1024, 1024), tuple(215 + x % 35 for x in color[:3]))
        draw = ImageDraw.Draw(image)
        draw.ellipse((500, 650, 1300, 1450), fill=tuple(40+x%100 for x in color[3:6]))
        draw.ellipse((-120, -160, 250, 200), fill=tuple(110+x%80 for x in color[6:9]))
        output = BytesIO(); image.save(output, format="PNG")
        return ImageResult(output.getvalue(), 1024, 1024, {"provider":"fixture", "model":"fixture-v1", "demo":True, "usage":{}, "cost_usd":0, "cost_is_estimate":False})
    if not settings.openai_api_key:
        raise ProviderError("AI_AUTH", "이미지 제공자 인증 설정이 필요합니다.")
    size = "1536x1024" if data["action"] == "image.generate.high" else "1024x1024"
    payload = {"model":settings.image_model, "prompt":design_prompt(data), "quality":"high", "size":size, "n":1, "output_format":"png"}
    headers = {"Authorization": "Bearer " + settings.openai_api_key}
    try:
        with httpx.Client(timeout=httpx.Timeout(240, connect=15), transport=transport) as client:
            if reference is not None:
                response = client.post("https://api.openai.com/v1/images/edits", headers=headers,
                    data={k:str(v) for k,v in payload.items()}, files={"image":("reference.png",reference,"image/png")})
            else:
                response = client.post("https://api.openai.com/v1/images/generations", headers=headers, json=payload)
    except httpx.TimeoutException:
        raise ProviderError("AI_TIMEOUT_UNCERTAIN", "제공자 응답을 확인하지 못해 예약 크레딧을 복원합니다. 중복 생성은 하지 않습니다.", uncertain=True) from None
    except httpx.TransportError:
        raise ProviderError("AI_CONNECTION_UNCERTAIN", "제공자 연결이 중단되어 결과를 확인 중입니다.", uncertain=True) from None
    request_id = response.headers.get("x-request-id")
    if response.status_code >= 400:
        status = response.status_code
        if status in (401,403):
            raise ProviderError("AI_AUTH", "이미지 제공자의 인증 또는 모델 권한을 확인해야 합니다.", request_id=request_id)
        if status == 429:
            raise ProviderError("AI_LIMIT", "이미지 제공자의 사용 한도에 도달했습니다. 예약은 복원됩니다.", request_id=request_id)
        if status >= 500:
            raise ProviderError("AI_TEMPORARY", "이미지 제공자에 일시적인 장애가 있습니다.", retryable=True, request_id=request_id)
        raise ProviderError("AI_REQUEST_REJECTED", "제공자가 요청을 처리하지 못했습니다. 디자인 설명이나 원본 이미지를 변경해 주세요.", request_id=request_id)
    try:
        result=response.json(); raw=base64.b64decode(result["data"][0]["b64_json"], validate=True)
        if not raw or len(raw)>25*1024*1024: raise ValueError()
        with Image.open(BytesIO(raw)) as image:
            width,height=image.size
            if image.format!="PNG" or width*height>40_000_000: raise ValueError()
            image.verify()
        usage=result.get("usage", {})
        details=usage.get("input_tokens_details", {})
        # Published Sunburst/Flare rates, USD per million tokens; preserve raw
        # usage and mark estimate. Do not confuse this with customer credits.
        known=all(k in usage for k in ("input_tokens", "output_tokens"))
        image_tokens=details.get("image_tokens",0)
        text_tokens=details.get("text_tokens",max(0,usage.get("input_tokens",0)-image_tokens))
        cost=((text_tokens*5 + image_tokens*8 + usage.get("output_tokens",0)*30)/1_000_000) if known else None
        return ImageResult(raw,width,height,{"provider":"openai", "model":settings.image_model,"quality":"high","size":size,
            "prompt_version":"packaging-background-v1","provider_request_id":request_id,"usage":usage,"cost_usd":cost,"cost_is_estimate":True,
            "warning":"AI 이미지의 임의 글자·형태를 확인하세요. 상품 문구와 바코드는 편집 객체로 입력해야 합니다."})
    except (ValueError,KeyError,IndexError,TypeError,OSError,Image.DecompressionBombError):
        raise ProviderError("AI_INVALID_RESULT", "사용 가능한 이미지가 전달되지 않아 예약을 복원합니다.", request_id=request_id) from None
