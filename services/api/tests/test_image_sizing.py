"""Resolution policy and HTTP adapter regression; no paid provider calls."""
import base64
from decimal import Decimal
from io import BytesIO
import json
import math

import httpx
from PIL import Image
import pytest

from services.api.config import Settings
from services.api.image_provider import ProviderError, generate_image, get_capabilities
from services.api.image_sizing import (
    ImageSizeError, MAX_EDGE, MAX_PIXELS, MIN_PIXELS, SIZE_POLICY_VERSION,
    frozen_image_output, select_image_output, valid_dimensions,
)


@pytest.mark.parametrize("model",["gpt-image-2.5-sunburst","gpt-image-2.5-flare"])
@pytest.mark.parametrize("width,height,expected",[
    (240,330,"2432x3344"),(330,240,"3344x2432"),
    (240,240,"2880x2880"),(100,300,"1280x3840"),(300,100,"3840x1280"),
])
def test_maximum_exact_ratio_within_documented_limits(model,width,height,expected):
    output=select_image_output(model,width,height)
    w,h=output["output_width_px"],output["output_height_px"]
    assert output["output_size"]==expected and w*height==h*width
    assert valid_dimensions(w,h) and output["output_experimental"] is True
    assert output["output_aspect_ratio_limited"] is False
    assert output["output_effective_ppi"]==round(min(w*25.4/width,h*25.4/height),2)
    assert not valid_dimensions(w+w//math.gcd(w,h)*16,h+h//math.gcd(w,h)*16)


@pytest.mark.parametrize("width,height",[(127,128),(239.9971,329.9123),(80,777),(599.99,30.01)])
def test_fractional_and_extreme_ratios_remain_large_bounded_and_deterministic(width,height):
    output=select_image_output("gpt-image-2.5-sunburst",width,height)
    w,h=output["output_width_px"],output["output_height_px"]
    ratio=min(3,max(1/3,width/height))
    attainable=min(MAX_PIXELS,MAX_EDGE**2/ratio,MAX_EDGE**2*ratio)
    assert valid_dimensions(w,h) and w*h>=attainable*.9
    assert output==select_image_output("gpt-image-2.5-sunburst",width,height)
    assert output["output_aspect_ratio_limited"]==(not 1/3<=width/height<=3)
    assert abs((w/h)/ratio-1)<.01


@pytest.mark.parametrize("bad",[None,True,0,-1,float("nan"),float("inf"),1e308,Decimal("1e-9999"),"240"])
def test_invalid_dimensions_cannot_reach_paid_provider(bad):
    with pytest.raises(ImageSizeError): select_image_output("gpt-image-2.5-sunburst",bad,330)


@pytest.mark.parametrize("model",["gpt-image-1","gpt-image-2","gpt-image-2.5","unknown"])
def test_unsupported_model_never_inherits_custom_size_capability(model):
    with pytest.raises(ImageSizeError): select_image_output(model,240,330)


def test_legacy_jobs_keep_original_fixed_size_and_new_snapshots_cannot_be_tampered():
    model="gpt-image-2.5-sunburst"
    for action,size in (("image.generate.standard","1024x1024"),("image.edit.standard","1024x1024"),("image.generate.high","1536x1024")):
        assert frozen_image_output(model,{"action":action,"width_mm":240,"height_mm":330})["output_size"]==size
    data={"width_mm":240,"height_mm":330,**select_image_output(model,240,330)}
    assert frozen_image_output(model,data)["output_size"]=="2432x3344"
    for patch in ({"output_size":"3840x3840"},{"output_size":"1024x1024"},{"size_policy_version":"unknown"}):
        with pytest.raises(ImageSizeError): frozen_image_output(model,{**data,**patch})
    with pytest.raises(ImageSizeError): frozen_image_output(model,{"output_size":"1024x1024"})


@pytest.mark.parametrize("model",["gpt-image-2.5-sunburst","gpt-image-2.5-flare"])
@pytest.mark.parametrize("action",["image.generate.standard","image.edit.standard"])
def test_generation_and_edit_send_frozen_native_resolution_and_measure_actual_usage(model,action):
    raw=BytesIO();Image.new("RGB",(32,48),"green").save(raw,format="PNG")
    settings=Settings(environment="test",ai_provider="openai",openai_api_key="mock-only",image_model=model)
    data={"action":action,"width_mm":240,"height_mm":330,"prompt":"Detailed original packaging illustration",**select_image_output(model,240,330)}
    calls=[]
    def transport(request):
        calls.append(request)
        if action=="image.edit.standard":
            assert request.url.path=="/v1/images/edits"
            body=request.content.decode("utf-8",errors="replace")
            assert 'name="size"\r\n\r\n2432x3344' in body and 'name="quality"\r\n\r\nhigh' in body
            assert 'name="image"; filename="reference.png"' in body and model in body
        else:
            assert request.url.path=="/v1/images/generations"
            body=json.loads(request.content)
            assert (body["size"],body["quality"],body["model"],body["n"])==("2432x3344","high",model,1)
        return httpx.Response(200,json={"data":[{"b64_json":base64.b64encode(raw.getvalue()).decode()}],"usage":{"input_tokens":1000,"input_tokens_details":{"text_tokens":800,"image_tokens":200},"output_tokens":24000}})
    result=generate_image(settings,data,raw.getvalue() if action=="image.edit.standard" else None,transport=httpx.MockTransport(transport))
    assert len(calls)==1 and (result.width,result.height)==(32,48)
    assert result.metadata["output_size"]=="2432x3344" and result.metadata["actual_size"]=="32x48"
    assert result.metadata["output_size_mismatch"] is True and result.metadata["output_experimental"] is True
    assert result.metadata["cost_usd"]==pytest.approx(.7256)
    assert result.metadata["cost_is_estimate"] is True and result.metadata["usage"]["output_tokens"]==24000


def test_invalid_frozen_size_is_rejected_before_network_and_capabilities_are_truthful():
    settings=Settings(environment="test",ai_provider="openai",openai_api_key="mock-only")
    data={"action":"image.generate.standard","width_mm":240,"height_mm":330,"output_size":"3840x3840","size_policy_version":SIZE_POLICY_VERSION}
    with pytest.raises(ProviderError) as caught:
        generate_image(settings,data,transport=httpx.MockTransport(lambda _:pytest.fail("must not call provider")))
    assert caught.value.code=="AI_SIZE_INVALID"
    capabilities=get_capabilities(settings)
    assert capabilities["standard"]["size"]=="face_aspect_max"
    assert capabilities["size_policy"]["experimental"] is True
    assert capabilities["size_policy"]["client_size_override"] is False
    assert capabilities["size_policy"]["print_ppi_guaranteed"] is False
    assert capabilities["size_policy"]["min_pixels"]==MIN_PIXELS
