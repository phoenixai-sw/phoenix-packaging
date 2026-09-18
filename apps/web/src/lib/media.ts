/** Set filmEnabled only after the approved MP4 is present in public/media. */
export const packagingMedia = {
  hero: "/media/packaging-hero.webp",
  matcha: "/media/concept-matcha.webp",
  duck: "/media/concept-duck.webp",
  citrus: "/media/concept-citrus.webp",
  berry: "/media/concept-berry.webp",
  film: "/media/packaging-film.mp4",
  filmPoster: "/media/packaging-film-poster.jpg",
  filmEnabled: true,
} as const;
export const designConcepts = [
  { key: "duck", image: packagingMedia.duck, index: "01 / PET & SNACK", title: "기분 좋은 간식, 오리 저키 스틱.", note: "따뜻한 오렌지 · 귀여운 캐릭터 · 걸이 구멍", alt: "따뜻한 오렌지와 오리 캐릭터로 표현한 오리 저키 스탠드 파우치 디자인 콘셉트" },
  { key: "citrus", image: packagingMedia.citrus, index: "02 / FRUIT & FRESH", title: "햇살을 담은, 제주 감귤칩.", note: "비비드 오렌지 · 손그림 감귤 · 3면 실링", alt: "비비드 오렌지와 손그림 감귤로 표현한 감귤칩 3면 실링 봉투 디자인 콘셉트" },
  { key: "berry", image: packagingMedia.berry, index: "03 / DAILY GOODNESS", title: "좋은 아침을 담은, 베리 그래놀라.", note: "라즈베리 핑크 · 투명 창 · 지퍼 파우치", alt: "라즈베리 핑크와 투명 창으로 표현한 베리 그래놀라 지퍼 파우치 디자인 콘셉트" },
  { key: "matcha", image: packagingMedia.matcha, index: "04 / TEA & WELLNESS", title: "일상의 속도를 낮추는, 말차 라떼.", note: "스프링 그린 · 잎 일러스트 · 카페 감성", alt: "스프링 그린과 잎 일러스트로 표현한 말차 라떼 파우치 디자인 콘셉트" },
] as const;
