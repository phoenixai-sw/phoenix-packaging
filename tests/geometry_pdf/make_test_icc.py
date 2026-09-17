"""Generate a synthetic CMYK CI profile, NOT a measured printing condition.

Original fixture generator and output dedicated to CC0-1.0. The simple
subtractive model intentionally makes no claim about any printer or substrate.
"""
from io import BytesIO
from itertools import product
from pathlib import Path
import struct
from PIL import Image, ImageCms


def make_profile():
    u16=lambda n:struct.pack(">H",n)
    u32=lambda n:struct.pack(">I",n)
    fixed=lambda n:struct.pack(">i",round(n*65536))
    srgb,lab=ImageCms.createProfile("sRGB"),ImageCms.createProfile("LAB",5000)
    rgb_lab=ImageCms.buildTransform(srgb,lab,"RGB","LAB")
    lab_rgb=ImageCms.buildTransform(lab,srgb,"LAB","RGB")
    def lut(inp,out,grid,values):
        return b"mft2"+b"\0"*4+bytes([inp,out,grid,0])+b"".join(fixed(v) for v in (1,0,0,0,1,0,0,0,1))+u16(2)+u16(2)+(u16(0)+u16(65535))*inp+b"".join(u16(v) for v in values)+(u16(0)+u16(65535))*out
    values=[]
    for c,m,y,k in product(range(9),repeat=4):
        rgb=tuple(round(255*(1-v/8)*(1-k/8)) for v in (c,m,y))
        pixel=ImageCms.applyTransform(Image.new("RGB",(1,1),rgb),rgb_lab).getpixel((0,0))
        values.extend(v*256 for v in pixel)
    a2b=lut(4,3,9,values)
    values=[]
    for l,a,b in product(range(17),repeat=3):
        pixel=tuple(round(v*255/16) for v in (l,a,b))
        r,g,b=ImageCms.applyTransform(Image.new("LAB",(1,1),pixel),lab_rgb).getpixel((0,0))
        k=255-max(r,g,b)
        cmy=[0,0,0] if k==255 else [(255-v-k)/(255-k) for v in (r,g,b)]
        values.extend(round(v*65535) for v in (*cmy,k/255))
    b2a=lut(3,4,17,values)
    def desc(s):
        value=s.encode('ascii')+b'\0'
        return b'desc'+b'\0'*4+u32(len(value))+value+b'\0'*8+b'\0'*70
    tags=[(b'desc',desc('Phoenix SYNTHETIC CMYK TEST ONLY - NOT FOR PRINTING')),
          (b'cprt',b'text'+b'\0'*4+b'Original synthetic test profile. CC0-1.0. No printer, material, ISO or manufacturer approval.\0'),
          (b'wtpt',b'XYZ '+b'\0'*4+b''.join(fixed(v) for v in (.9642,1,.8249))),
          (b'A2B0',a2b),(b'A2B1',a2b),(b'B2A0',b2a),(b'B2A1',b2a)]
    offset=132+12*len(tags); table=b'';payload=b''
    for signature,data in tags:
        table+=signature+u32(offset)+u32(len(data))
        padded=data+b'\0'*((-len(data))%4);payload+=padded;offset+=len(padded)
    header=bytearray(128)
    header[:4]=u32(offset);header[4:8]=b'lcms';header[8:12]=bytes([2,0x40,0,0])
    header[12:24]=b'prtrCMYKLab ';header[24:36]=struct.pack('>6H',2026,9,18,0,0,0)
    header[36:40]=b'acsp';header[64:68]=u32(1);header[68:80]=b''.join(fixed(v) for v in (.9642,1,.8249));header[80:84]=b'PHNX'
    return bytes(header)+u32(len(tags))+table+payload


if __name__=='__main__':
    path=Path(__file__).resolve().parents[2]/'fixtures'/'icc'/'synthetic-cmyk-test.icc'
    path.parent.mkdir(exist_ok=True);path.write_bytes(make_profile())
    print('Synthetic, non-manufacturing test ICC created:',path.name)
