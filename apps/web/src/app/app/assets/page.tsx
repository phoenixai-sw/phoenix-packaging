import { AssetLibrary } from "@/components/asset-library";
import { ManagementPage } from "@/components/management";
export default function AssetsPage() {
  return <ManagementPage eyebrow="IMAGE LIBRARY" title="이미지 보관함" description="제품 사진·로고·AI 시안을 찾아 원본을 확인하세요. 편집기의 이미지 보관함에서 현재 면에 다시 배치할 수 있습니다.">
    <AssetLibrary />
  </ManagementPage>;
}
