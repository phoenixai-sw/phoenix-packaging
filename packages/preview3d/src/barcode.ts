/** Preview-only mirror of the pinned ReportLab EAN-13 encoder, parity tested against its bars. */
const L = ["0001101","0011001","0010011","0111101","0100011","0110001","0101111","0111011","0110111","0001011"];
const G = ["0100111","0110011","0011011","0100001","0011101","0111001","0000101","0010001","0001001","0010111"];
const R = ["1110010","1100110","1101100","1000010","1011100","1001110","1010000","1000100","1001000","1110100"];
const PARITY = ["LLLLLL","LLGLGG","LLGGLG","LLGGGL","LGLLGG","LGGLLG","LGGGLL","LGLGLG","LGLGGL","LGGLGL"];
export function ean13Geometry(value: string, moduleMm = .33, barHeightMm = 22.85) {
  if (!/^[0-9]{13}$/.test(value)) throw new Error("선행 0을 포함한 EAN-13 13자리 문자열을 입력해 주세요.");
  const sum = [...value.slice(0,12)].reduce((total,n,i)=>total+Number(n)*(i%2?3:1),0);
  if ((10-sum%10)%10 !== Number(value[12])) throw new Error("체크 숫자가 일치하지 않습니다.");
  if (!Number.isFinite(moduleMm)||moduleMm<.264||moduleMm>.66||!Number.isFinite(barHeightMm)||barHeightMm+1e-9<22.85*moduleMm/.33||barHeightMm>45.7) throw new Error("바코드 모듈 폭 또는 높이가 소비자용 EAN-13 허용 범위를 벗어났습니다.");
  const parity=PARITY[Number(value[0])];
  const bits="101"+[...value.slice(1,7)].map((n,i)=>(parity[i]==="L"?L:G)[Number(n)]).join("")+"01010"+[...value.slice(7)].map(n=>R[Number(n)]).join("")+"101";
  const bars: {x_mm:number;width_mm:number}[]=[];
  for(let i=0;i<bits.length;i++) {
    if(bits[i]!=="1") continue;
    const start=i;while(bits[i+1]==="1")i++;
    bars.push({x_mm:Number(((start+11)*moduleMm).toFixed(6)),width_mm:Number(((i-start+1)*moduleMm).toFixed(6))});
  }
  return {value,symbology:"EAN13",module_mm:moduleMm,bar_height_mm:barHeightMm,width_mm:Number((113*moduleMm).toFixed(4)),height_mm:Number((barHeightMm+5).toFixed(4)),quiet_left_mm:11*moduleMm,quiet_right_mm:7*moduleMm,bars,foreground:"#000000",background:"#ffffff"};
}
