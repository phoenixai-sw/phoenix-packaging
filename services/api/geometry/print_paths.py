"""Bounded rectangular union boundary. Shared edges are folds, never duplicate cuts."""
from .validation import GeometryValidationError


def structure_paths(geometry, layout):
    from .finishing_paths import add_finishing_paths
    if layout == "face_pages":
        return add_finishing_paths([{"face_id":f["id"],"width_mm":f["width_mm"],"height_mm":f["height_mm"],
                 "cut":[[0,0,f["width_mm"],0],[f["width_mm"],0,f["width_mm"],f["height_mm"]],
                        [f["width_mm"],f["height_mm"],0,f["height_mm"]],[0,f["height_mm"],0,0]],
                 "fold":[[p[k] for k in ("x1_mm","y1_mm","x2_mm","y2_mm")] for p in f["regions"].get("fold",[])]}
                for f in geometry["faces"]],geometry,layout)
    if not all(f.get("registered_structure") for f in geometry["faces"]):
        raise GeometryValidationError("REGISTERED_NET_REQUIRED", "전개도 제작 엔진은 등록된 고정 구조만 지원합니다.")
    rects=[(f["net"]["x_mm"],f["net"]["y_mm"],f["width_mm"],f["height_mm"]) for f in geometry["faces"]]
    rects += [(p["x_mm"],p["y_mm"],p["width_mm"],p["height_mm"]) for p in geometry.get("structural_parts",[])]
    xs=sorted({round(x,4) for x,y,w,h in rects}|{round(x+w,4) for x,y,w,h in rects})
    ys=sorted({round(y,4) for x,y,w,h in rects}|{round(y+h,4) for x,y,w,h in rects})
    if len(xs)>64 or len(ys)>64:raise GeometryValidationError("PRINT_PATH_LIMIT","전개도 경계 계산 한도를 넘었습니다.")
    cells=set();owners={}
    for i in range(len(xs)-1):
        for j in range(len(ys)-1):
            cx,cy=(xs[i]+xs[i+1])/2,(ys[j]+ys[j+1])/2
            containing=[k for k,(x,y,w,h) in enumerate(rects) if x<cx<x+w and y<cy<y+h]
            if len(containing)>1:raise GeometryValidationError("PRINT_PANEL_OVERLAP","인쇄 전개도의 패널 영역이 겹칩니다.")
            if containing:cells.add((i,j));owners[(i,j)]=containing[0]
    cuts=[]
    for i,j in sorted(cells):
        a,b,c,d=xs[i],ys[j],xs[i+1],ys[j+1]
        for neighbor,line in (((i,j-1),[a,b,c,b]),((i+1,j),[c,b,c,d]),((i,j+1),[c,d,a,d]),((i-1,j),[a,d,a,b])):
            if neighbor not in cells:cuts.append(line)
    # A proper rectangle union has exactly one incoming/outgoing edge at each vertex.
    from collections import Counter
    starts=Counter(tuple(p[:2]) for p in cuts)
    if starts!=Counter(tuple(p[2:]) for p in cuts) or any(n!=1 for n in starts.values()):
        raise GeometryValidationError("PRINT_CUT_OPEN","칼선 외곽이 닫히지 않았습니다.")
    folds=[]
    for p in geometry.get("fold_lines",[]):folds.append([p[k] for k in ("x1_mm","y1_mm","x2_mm","y2_mm")])
    for f in geometry["faces"]:
        for p in f["regions"].get("fold",[]):
            points=[]
            for x,y in ((p["x1_mm"],p["y1_mm"]),(p["x2_mm"],p["y2_mm"])):
                if f["net"]["rotation_deg"]==180:x,y=f["width_mm"]-x,f["height_mm"]-y
                points.extend((round(f["net"]["x_mm"]+x,4),round(f["net"]["y_mm"]+y,4)))
            folds.append(points)
    unique={tuple(sorted((tuple(line[:2]),tuple(line[2:])))):line for line in folds}
    for line in unique.values():
        vertical=line[0]==line[2];horizontal=line[1]==line[3]
        if not (vertical or horizontal):raise GeometryValidationError("PRINT_DIAGONAL_FOLD_UNSUPPORTED","이 직사각 출력기는 수평·수직 접힘선만 지원합니다.")
        axis=1 if vertical else 0;low,high=sorted((line[axis],line[axis+2]))
        breaks=sorted({low,high}|{v for v in (ys if vertical else xs) if low<v<high})
        for a,b in zip(breaks,breaks[1:]):
            cx,cy=(line[0],(a+b)/2) if vertical else ((a+b)/2,line[1])
            if not any(x<=cx<=x+w and y<=cy<=y+h for x,y,w,h in rects):
                raise GeometryValidationError("PRINT_FOLD_OUTSIDE_MATERIAL","접힘선이 전개도 용지 밖의 빈 공간을 통과합니다.")
    def collinear_overlap(a,b):
        if a[0]==a[2]==b[0]==b[2]:return max(min(a[1],a[3]),min(b[1],b[3]))<min(max(a[1],a[3]),max(b[1],b[3]))
        if a[1]==a[3]==b[1]==b[3]:return max(min(a[0],a[2]),min(b[0],b[2]))<min(max(a[0],a[2]),max(b[0],b[2]))
        return False
    if any(collinear_overlap(cut,fold) for cut in cuts for fold in unique.values()):
        raise GeometryValidationError("PRINT_CUT_FOLD_OVERLAP","CUT 외곽에 겹치는 FOLD 선을 지정할 수 없습니다.")
    # Touching flap edges need a slit unless an explicit crease covers them.
    # Union-only outlines would silently join separate flaps into a solid strip.
    for i,j in sorted(cells):
        for neighbor,line in (((i+1,j),[xs[i+1],ys[j],xs[i+1],ys[j+1]]),((i,j+1),[xs[i],ys[j+1],xs[i+1],ys[j+1]])):
            if neighbor not in owners or owners[neighbor]==owners[(i,j)]:continue
            vertical=line[0]==line[2];axis=1 if vertical else 0
            low,high=sorted((line[axis],line[axis+2]));intervals=[]
            for fold in unique.values():
                if collinear_overlap(line,fold):intervals.append((max(low,min(fold[axis],fold[axis+2])),min(high,max(fold[axis],fold[axis+2]))))
            cursor=low
            for start,end in sorted(intervals)+[(high,high)]:
                if start>cursor:
                    cuts.append([line[0],cursor,line[0],start] if vertical else [cursor,line[1],start,line[1]])
                cursor=max(cursor,end)
    return add_finishing_paths([{"face_id":"net","width_mm":geometry["net_width_mm"],"height_mm":geometry["net_height_mm"],"cut":cuts,"fold":list(unique.values()),"rectangles":rects}],geometry,layout)
