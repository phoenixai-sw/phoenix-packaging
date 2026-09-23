"""Actual ICC transforms; bytes are supplied only by the authorized registry resolver."""
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageCms, ImageOps, ImageStat
from reportlab.lib.colors import CMYKColor
from reportlab.lib.utils import ImageReader
from .review_pdf import ExportValidationError

MAX_ICC_BYTES = 4 * 1024 * 1024


def inspect_icc(raw: bytes):
    try:
        if not isinstance(raw, bytes) or not 132 <= len(raw) <= MAX_ICC_BYTES:
            raise ValueError()
        if int.from_bytes(raw[:4], "big") != len(raw) or raw[36:40] != b"acsp" or raw[12:16] != b"prtr" or raw[16:20] != b"CMYK":
            raise ValueError()
        count = int.from_bytes(raw[128:132], "big")
        if count > 256 or 132+12*count > len(raw): raise ValueError()
        for index in range(count):
            tag = raw[132+12*index:144+12*index]
            offset, size = int.from_bytes(tag[4:8], "big"), int.from_bytes(tag[8:12], "big")
            if size < 8 or offset < 132+12*count or offset+size > len(raw): raise ValueError()
        profile = ImageCms.ImageCmsProfile(BytesIO(raw))
        ImageCms.buildTransform(ImageCms.createProfile("sRGB"), profile, "RGB", "CMYK")
        return {"sha256": sha256(raw).hexdigest(), "byte_size": len(raw),
                "description": ImageCms.getProfileDescription(profile).strip()[:500],
                "copyright": profile.profile.copyright[:2000], "color_space": "CMYK", "device_class": "prtr"}
    except Exception as exc:
        raise ExportValidationError("INVALID_CMYK_ICC", "변환 가능한 CMYK 출력 장치 ICC 파일(최대 4MiB)이 필요합니다.", "icc") from exc


class PrintColor:
    def __init__(self, raw, profile):
        self.info = inspect_icc(raw)
        if self.info["sha256"] != profile["icc_sha256"]:
            raise ExportValidationError("ICC_HASH_MISMATCH", "동결된 ICC 해시와 파일이 일치하지 않습니다.")
        self.raw, self.profile = raw, profile
        self.output = ImageCms.ImageCmsProfile(BytesIO(raw))
        self.input = ImageCms.createProfile("sRGB")
        self.intent = ImageCms.Intent.RELATIVE_COLORIMETRIC if profile["rendering_intent"] == "relative_colorimetric" else ImageCms.Intent.PERCEPTUAL
        self.flags = ImageCms.Flags.BLACKPOINTCOMPENSATION if profile["black_point_compensation"] else ImageCms.Flags.NONE
        self.transform = ImageCms.buildTransform(self.input, self.output, "RGB", "CMYK", self.intent, self.flags)
        self.images, self.records = {}, []

    def _check_ink(self, image):
        # A histogram of a 32-bit sum avoids overflow and scans every pixel.
        from PIL import ImageMath
        channels = [channel.convert("I") for channel in image.split()]
        summed = ImageMath.lambda_eval(lambda x: x["a"]+x["b"]+x["c"]+x["d"], **dict(zip("abcd", channels)))
        maximum = summed.getextrema()[1]*100/255
        if maximum > self.profile["max_ink_percent"] + .8:
            raise ExportValidationError("TOTAL_INK_LIMIT", "변환된 색의 총잉크량이 인쇄 프로필 한도를 넘습니다. ICC와 한도를 확인해 주세요.")
        return round(maximum, 4)

    @lru_cache(maxsize=4096)
    def color(self, color):
        values = tuple(round(channel*255) for channel in (color.red, color.green, color.blue))
        if values == (0, 0, 0): return CMYKColor(0, 0, 0, 1)
        if values == (255, 255, 255): return CMYKColor(0, 0, 0, 0)
        image = ImageCms.applyTransform(Image.new("RGB", (1, 1), values), self.transform)
        self._check_ink(image)
        return CMYKColor(*(value/255 for value in image.getpixel((0, 0))))

    def image(self, asset_id, resolver):
        if asset_id in self.images: return self.images[asset_id]
        if resolver is None: raise ExportValidationError("ASSET_UNAVAILABLE", "이미지 자산이 필요합니다.")
        raw = resolver(asset_id)
        if isinstance(raw, Path): raw = raw.read_bytes()
        if not isinstance(raw, bytes) or len(raw) > 20*1024*1024:
            raise ExportValidationError("ASSET_UNAVAILABLE", "허가된 이미지 바이트가 필요합니다.")
        try:
            with Image.open(BytesIO(raw)) as source:
                if source.format not in ("PNG", "JPEG", "WEBP") or source.width*source.height > 40_000_000: raise ValueError()
                source.load(); image = ImageOps.exif_transpose(source)
                if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                    if image.convert("RGBA").getchannel("A").getextrema() != (255, 255):
                        raise ExportValidationError("PRINT_TRANSPARENCY_UNSUPPORTED", "반투명 이미지는 이 제작 어댑터에서 지원하지 않습니다. 편집기의 “레이어 병합”에서 아래 레이어와 합쳐 주세요.")
                embedded = source.info.get("icc_profile")
                if embedded:
                    source_profile = ImageCms.ImageCmsProfile(BytesIO(embedded))
                    mode = source_profile.profile.xcolor_space.strip()
                    if mode not in ("RGB", "CMYK") or (mode == "CMYK" and image.mode != "CMYK"): raise ValueError()
                    image = image.convert(mode)
                    image = ImageCms.profileToProfile(image, source_profile, self.output, outputMode="CMYK", renderingIntent=self.intent, flags=self.flags)
                    input_hash = sha256(embedded).hexdigest()
                else:
                    if image.mode == "CMYK": raise ExportValidationError("UNTAGGED_CMYK", "CMYK 원본에는 입력 ICC가 필요합니다.")
                    image = ImageCms.applyTransform(image.convert("RGB"), self.transform); input_hash = "sRGB-explicit-policy"
                maximum = self._check_ink(image)
                image.load()
        except ExportValidationError: raise
        except Exception as exc: raise ExportValidationError("INVALID_PRINT_IMAGE", "이미지 입력 색공간·ICC·파일을 확인해 주세요.") from exc
        result = (ImageReader(image), image.size)
        self.images[asset_id] = result
        self.records.append({"asset_id":asset_id,"source_sha256":sha256(raw).hexdigest(),"input_profile":input_hash,
                             "output_profile_sha256":self.info["sha256"],"output_mode":"CMYK","pixels":list(image.size),"maximum_ink_percent":maximum})
        return result
