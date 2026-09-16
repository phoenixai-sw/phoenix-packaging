"use client";

import { useEffect, useRef, useState } from "react";
import { Pause, Play, ArrowUpRight } from "lucide-react";
import Link from "next/link";

export function BrandFilm({
  enabled,
  source,
  poster,
}: {
  enabled: boolean;
  source: string;
  poster: string;
}) {
  const section = useRef<HTMLElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const [reducedMotion, setReducedMotion] = useState(true);
  const [inView, setInView] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [manualPlay, setManualPlay] = useState(false);
  const [pausedByUser, setPausedByUser] = useState(false);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(preference.matches);
    update();
    preference.addEventListener("change", update);
    const observer = new IntersectionObserver(
      (entries) => setInView(entries[0].isIntersecting),
      { threshold: 0.25 },
    );
    if (section.current) observer.observe(section.current);
    return () => {
      preference.removeEventListener("change", update);
      observer.disconnect();
    };
  }, [enabled]);

  useEffect(() => {
    if (enabled && inView && (!reducedMotion || manualPlay)) setLoaded(true);
  }, [enabled, inView, reducedMotion, manualPlay]);

  useEffect(() => {
    const element = video.current;
    if (!element || !loaded) return;
    if (inView && (!reducedMotion || manualPlay) && !pausedByUser) {
      void element.play().catch(() => setPlaying(false));
    } else element.pause();
  }, [loaded, inView, reducedMotion, manualPlay, pausedByUser]);

  if (!enabled) return null;

  function togglePlayback() {
    if (playing) {
      setPausedByUser(true);
      video.current?.pause();
    } else {
      setLoaded(true);
      setManualPlay(true);
      setPausedByUser(false);
      if (video.current?.src)
        void video.current.play().catch(() => setPlaying(false));
    }
  }

  return (
    <section
      className="brand-film-section section-wrap"
      ref={section}
      aria-labelledby="brand-film-title"
    >
      <div className="film-heading">
        <div>
          <span className="eyebrow">THE PHOENIX EDIT</span>
          <h2 id="brand-film-title">
            평범한 진열대 위,
            <br />
            조금 특별한 존재감.
          </h2>
        </div>
        <p>
          색과 질감, 빛이 만나는 순간.
          <br />
          우리가 상상하는 패키지의 다음 모습을 만나보세요.
        </p>
      </div>
      <div className="brand-film-frame">
        <video
          ref={video}
          src={loaded && !unavailable ? source : undefined}
          poster={poster}
          muted
          loop
          playsInline
          autoPlay={!reducedMotion}
          preload="none"
          aria-hidden="true"
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onError={() => {
            setUnavailable(true);
            setPlaying(false);
          }}
        />
        <div className="film-overlay">
          <span>PHOENIX PACKAGING</span>
          <span>BRAND FILM / 01</span>
        </div>
        {!unavailable && (
          <button
            type="button"
            className="film-playback"
            onClick={togglePlayback}
            aria-label={playing ? "브랜드 영상 일시정지" : "브랜드 영상 재생"}
          >
            {playing ? <Pause size={15} /> : <Play size={15} />}
            <span>{playing ? "일시정지" : "영상 재생"}</span>
          </button>
        )}
      </div>
      <div className="film-caption">
        <p>AI로 제작한 디자인 콘셉트 영상 · 소리 없이 재생됩니다.</p>
        <Link href="/app/projects/new">
          우리 브랜드의 시작 <ArrowUpRight size={16} />
        </Link>
      </div>
    </section>
  );
}
