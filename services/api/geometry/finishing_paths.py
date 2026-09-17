"""Audited circular holes and V/U side notches, expressed in physical millimetres.

Quarter ellipses use the standard cubic approximation. Maximum radial error is
below 0.0014 mm at the supported 5 mm radius (our PDF tolerance is 0.01 mm).
No arbitrary SVG, perforation, or rounded package corner is accepted here.
"""
from copy import deepcopy
from math import sqrt
from .validation import GeometryValidationError

K=4*(sqrt(2)-1)/3


def circle_curves(x,y,r):
    return [[x+r,y,x+r,y+K*r,x+K*r,y+r,x,y+r],
            [x,y+r,x-K*r,y+r,x-r,y+K*r,x-r,y],
            [x-r,y,x-r,y-K*r,x-K*r,y-r,x,y-r],
            [x,y-r,x+K*r,y-r,x+r,y-K*r,x+r,y]]


def notch_segments(width,feature,side):
    y=feature['tear_y_mm'];a=feature['notch_height_mm']/2;d=feature['notch_depth_mm']
    edge=0 if side=='left' else width;sign=1 if side=='left' else -1
    if feature['notch_shape']=='v':return {'lines':[[edge,y-a,edge+sign*d,y],[edge+sign*d,y,edge,y+a]],'curves':[]}
    return {'lines':[],'curves':[[edge,y-a,edge+sign*K*d,y-a,edge+sign*d,y-K*a,edge+sign*d,y],
                               [edge+sign*d,y,edge+sign*d,y+K*a,edge+sign*K*d,y+a,edge,y+a]]}


def _map(points,face,net):
    result=[]
    for x,y in zip(points[::2],points[1::2]):
        if net:
            if face['net'].get('rotation_deg',0)==180:x,y=face['width_mm']-x,face['height_mm']-y
            x+=face['net']['x_mm'];y+=face['net']['y_mm']
        result.extend((round(x,8),round(y,8)))
    return result


def _splice_notch(page,face,feature,side,net):
    edge=0 if side=='left' else face['width_mm'];y=feature['tear_y_mm'];half=feature['notch_height_mm']/2
    x,low,_,high=_map([edge,y-half,edge,y+half],face,net);low,high=sorted((low,high))
    # Both sides of a notch must really be exterior, not an internal panel seam.
    outward=(-1 if side=='left' else 1)*(-1 if net and face['net'].get('rotation_deg',0)==180 else 1)
    if net and any(rx<x+outward*.001<rx+rw and max(ry,low)<min(ry+rh,high) for rx,ry,rw,rh in page['rectangles']):
        raise GeometryValidationError('FINISHING_NOTCH_NOT_EXTERIOR','노치가 전개도의 외곽이 아닌 연결 면·부품과 겹칩니다.')
    covered=[];replacement=[]
    for line in page['cut']:
        x1,y1,x2,y2=line;a,b=sorted((y1,y2))
        if abs(x1-x)>1e-6 or abs(x2-x)>1e-6 or min(b,high)<=max(a,low):replacement.append(line);continue
        covered.append((max(a,low),min(b,high)))
        for start,end in ((a,min(b,low)),(max(a,high),b)):
            if end>start:replacement.append([x,start,x,end] if y1<y2 else [x,end,x,start])
    cursor=low
    for a,b in sorted(covered):
        if a>cursor+.0001:break
        cursor=max(cursor,b)
    if cursor<high-.0001:raise GeometryValidationError('FINISHING_NOTCH_NOT_EXTERIOR','노치 전체가 검증된 외곽 CUT에 놓여야 합니다.')
    page['cut']=replacement
    parts=notch_segments(face['width_mm'],feature,side)
    page['cut'].extend(_map(p,face,net) for p in parts['lines'])
    page['cut_curves'].extend(_map(p,face,net) for p in parts['curves'])
    page['notches'].append({'face_id':face['id'],'side':side,'shape':feature['notch_shape'],
                            'depth_mm':feature['notch_depth_mm'],'height_mm':feature['notch_height_mm'],
                            'edge_endpoints_mm':[x,low,x,high]})


def add_finishing_paths(pages,geometry,layout):
    if not geometry.get('holes') and geometry.get('pouch_features') is None:return pages
    if geometry['template_id'] not in ('three-side-seal','stand-up-pouch'):
        raise GeometryValidationError('PRINT_FINISHING_UNSUPPORTED','지원하는 파우치의 원형 걸이 구멍·V/U 측면 노치만 출력할 수 있습니다.')
    pages=deepcopy(pages);net=layout=='net';feature=geometry.get('pouch_features')
    for page in pages:page.update(cut_curves=[],holes=[],notches=[],process=[])
    for face in geometry['faces']:
        page=pages[0] if net else next(p for p in pages if p['face_id']==face['id'])
        regions=face['regions']
        for hole in regions.get('hole',[]):
            if hole.get('kind')!='circle':raise GeometryValidationError('PRINT_FINISHING_UNSUPPORTED','원형이 아닌 걸이 구멍은 지원하지 않습니다.')
            x,y,r=hole['center_x_mm'],hole['center_y_mm'],hole['radius_mm']
            page['cut_curves'].extend(_map(c,face,net) for c in circle_curves(x,y,r))
            cx,cy=_map([x,y],face,net)
            page['holes'].append({'face_id':face['id'],'center_x_mm':cx,'center_y_mm':cy,'diameter_mm':2*r})
        if feature is not None and face['id'] in ('front','back'):
            if feature['tear_enabled']:
                for side in ('left','right'):_splice_notch(page,face,feature,side,net)
            for kind,key in (('header_reference','header'),('zipper_band','zipper')):
                rect=regions.get(key)
                if not rect:continue
                if key=='zipper':rect=rect['band']
                x,y,w,h=(rect[k] for k in ('x_mm','y_mm','width_mm','height_mm'))
                page['process'].append({'face_id':face['id'],'kind':kind,'polygon_mm':_map([x,y,x+w,y,x+w,y+h,x,y+h],face,net)})
            for kind,line in (('tear_reference',regions.get('tear_line')),('zipper_center',(regions.get('zipper') or {}).get('line'))):
                if line:page['process'].append({'face_id':face['id'],'kind':kind,'line_mm':_map([line[k] for k in ('x1_mm','y1_mm','x2_mm','y2_mm')],face,net)})
        for rect in regions.get('seal',[]):
            x,y,w,h=(rect[k] for k in ('x_mm','y_mm','width_mm','height_mm'))
            page['process'].append({'face_id':face['id'],'kind':'seal_region','polygon_mm':_map([x,y,x+w,y,x+w,y+h,x,y+h],face,net)})
    for page in pages:
        # A crease through a punched opening or the zipper attachment band is
        # not supported by this adapter, even when supplied in a fixed net.
        for x1,y1,x2,y2 in page['fold']:
            for hole in page['holes']:
                x,y,r=hole['center_x_mm'],hole['center_y_mm'],hole['diameter_mm']/2
                distance=((x-max(min(x1,x2),min(x,max(x1,x2))))**2+(y-max(min(y1,y2),min(y,max(y1,y2))))**2)**.5
                if distance<r+.01:raise GeometryValidationError('FINISHING_FOLD_COLLISION','접힘선이 원형 타공과 겹칩니다.')
            boxes=[]
            for notch in page['notches']:
                x,low,_,high=notch['edge_endpoints_mm'];d=notch['depth_mm']
                boxes.append((x-d,low,x+d,high))
            for record in page['process']:
                if record['kind']=='zipper_band':
                    pts=record['polygon_mm'];boxes.append((min(pts[::2]),min(pts[1::2]),max(pts[::2]),max(pts[1::2])))
            if any(max(min(x1,x2),left)<min(max(x1,x2),right) and top<=y1==y2<=bottom or
                   max(min(y1,y2),top)<min(max(y1,y2),bottom) and left<=x1==x2<=right for left,top,right,bottom in boxes):
                raise GeometryValidationError('FINISHING_FOLD_COLLISION','접힘선이 노치·지퍼 부착 대역과 겹칩니다.')
    return pages


def expected_segments(page,role):
    """Single source for drawing and independent final-byte path measurements."""
    if role=='cut':return {'lines':page['cut'],'curves':page.get('cut_curves',[])}
    if role=='fold':return {'lines':page['fold'],'curves':[]}
    lines=[]
    for item in page.get('process',[]):
        if 'line_mm' in item:lines.append(item['line_mm'])
        else:
            points=item['polygon_mm'];pairs=list(zip(points[::2],points[1::2]))
            lines.extend([*a,*b] for a,b in zip(pairs,pairs[1:]+pairs[:1]))
    return {'lines':lines,'curves':[]}
