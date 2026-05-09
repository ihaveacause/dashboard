/**
 * I Have a Cause — VideoShort (Sprint 5)
 * 60-second portrait video (1080×1920) for YouTube Shorts
 *
 * Timeline (30fps):
 *  0:00–0:03  → Hook text + dramatic music hit         (0–89)
 *  0:03–0:15  → Scene 1: stock footage + subtitles    (90–449)
 *  0:15–0:30  → Scene 2: stock footage + subtitles    (450–899)
 *  0:30–0:45  → Scene 3: stock footage + subtitles    (900–1349)
 *  0:45–0:57  → Scene 4: stock footage + subtitles    (1350–1709)
 *  0:57–1:00  → Channel branding + subscribe          (1710–1799)
 */

import React from 'react';
import {
  AbsoluteFill, Audio, Sequence, Video,
  useCurrentFrame, useVideoConfig,
  interpolate, Easing, spring, staticFile,
} from 'remotion';

// ── Frame constants (30 fps) ─────────────────────────────────────────────────
const FPS          = 30;
const HOOK_END     = 90;    // 3 sec
const S1_START     = 90;
const S2_START     = 450;
const S3_START     = 900;
const S4_START     = 1350;
const BRAND_START  = 1710;
const TOTAL        = 1800;

const SCENE_STARTS    = [S1_START, S2_START, S3_START, S4_START, BRAND_START];
const SCENE_DURATIONS = [360, 450, 450, 360]; // sum = 1620 - 90 (hook) = 1530 ✓

// ── Palette ──────────────────────────────────────────────────────────────────
const C = {
  accent : '#e8412a',
  accent2: '#ff6b4a',
  white  : '#ffffff',
  dark   : '#0d0f14',
  gold   : '#f1c40f',
  overlay: 'rgba(0,0,0,0.58)',
  subBg  : 'rgba(0,0,0,0.76)',
};

// ── Easing helpers ───────────────────────────────────────────────────────────
const fadeIn  = (f, d = 15) =>
  interpolate(f, [0, d], [0, 1], { extrapolateRight: 'clamp' });
const fadeOut = (f, total, d = 12) =>
  interpolate(f, [total - d, total], [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
const slideUp = (f, d = 20, distance = 40) =>
  interpolate(f, [0, d], [distance, 0], {
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

// ── Hook Scene (frames 0–89) ─────────────────────────────────────────────────
const HookScene = ({ hookText }) => {
  const f = useCurrentFrame();

  const lineWidth = interpolate(f, [5, 35], [0, 220], { extrapolateRight: 'clamp' });
  const textOpacity = fadeIn(f, 12);
  const badgeOpacity = interpolate(f, [25, 40], [0, 1], { extrapolateRight: 'clamp' });
  const textY = slideUp(f, 18, 50);

  return (
    <AbsoluteFill
      style={{
        background: 'linear-gradient(160deg, #0d0f14 0%, #1a0a0a 60%, #2d0808 100%)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '60px 55px',
      }}
    >
      {/* Red accent line */}
      <div style={{
        width: lineWidth,
        height: 5,
        background: `linear-gradient(90deg, ${C.accent}, ${C.accent2})`,
        borderRadius: 3,
        marginBottom: 44,
      }} />

      {/* Hook text */}
      <div style={{
        fontSize: hookText && hookText.length > 60 ? 58 : 70,
        fontWeight: 900,
        color: C.white,
        textAlign: 'center',
        lineHeight: 1.18,
        letterSpacing: '-0.5px',
        transform: `translateY(${textY}px)`,
        opacity: textOpacity,
        textShadow: '0 4px 24px rgba(0,0,0,0.6)',
        fontFamily: "'Noto Sans Tamil', 'Noto Sans', system-ui, sans-serif",
      }}>
        {hookText || 'Breaking News'}
      </div>

      {/* BREAKING badge */}
      <div style={{
        marginTop: 42,
        padding: '12px 32px',
        background: C.accent,
        borderRadius: 40,
        opacity: badgeOpacity,
        display: 'flex',
        alignItems: 'center',
        gap: 10,
      }}>
        <div style={{
          width: 12, height: 12,
          borderRadius: '50%',
          background: C.white,
          animation: 'none',
        }} />
        <span style={{
          fontSize: 30,
          fontWeight: 800,
          color: C.white,
          letterSpacing: 2,
          textTransform: 'uppercase',
        }}>
          BREAKING
        </span>
      </div>
    </AbsoluteFill>
  );
};

// ── Story Scene ──────────────────────────────────────────────────────────────
const StoryScene = ({ scene, sceneIndex, duration }) => {
  const f = useCurrentFrame();

  const opacity  = fadeIn(f, 14) * fadeOut(f, duration, 14);
  const scale    = interpolate(f, [0, duration], [1.0, 1.07], { extrapolateRight: 'clamp' });
  const numOpacity = interpolate(f, [5, 22], [0, 1], { extrapolateRight: 'clamp' });

  // Fallback gradient when no video
  const placeholderColors = [
    'linear-gradient(135deg,#1a1a2e,#16213e)',
    'linear-gradient(135deg,#16213e,#0f3460)',
    'linear-gradient(135deg,#0f3460,#533483)',
    'linear-gradient(135deg,#533483,#2d1b69)',
  ];

  return (
    <AbsoluteFill style={{ opacity }}>
      {/* Background video or coloured placeholder */}
      <AbsoluteFill style={{ overflow: 'hidden', background: placeholderColors[sceneIndex] }}>
        {scene.videoFile && (
          <Video
            src={staticFile(scene.videoFile)}
            style={{
              width: '100%', height: '100%',
              objectFit: 'cover',
              transform: `scale(${scale})`,
            }}
            muted
          />
        )}
        {/* Dark overlay for text readability */}
        <AbsoluteFill style={{ background: C.overlay }} />
      </AbsoluteFill>

      {/* Scene number dot — top right */}
      <div style={{
        position: 'absolute',
        top: 55, right: 44,
        width: 58, height: 58,
        borderRadius: '50%',
        background: C.accent,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 26, fontWeight: 800, color: C.white,
        opacity: numOpacity,
        boxShadow: '0 4px 16px rgba(232,65,42,0.5)',
      }}>
        {sceneIndex + 1}
      </div>
    </AbsoluteFill>
  );
};

// ── Subtitle Layer ───────────────────────────────────────────────────────────
const SubtitleLayer = ({ subtitles }) => {
  const f = useCurrentFrame();
  const current = subtitles.find(s => f >= s.start && f <= s.end);
  if (!current) return null;

  const local = f - current.start;
  const dur   = current.end - current.start;
  const op    = fadeIn(local, 4) * fadeOut(local, dur, 4);

  return (
    <AbsoluteFill
      style={{
        display: 'flex', alignItems: 'flex-end', justifyContent: 'center',
        padding: '0 36px 175px',
        pointerEvents: 'none',
      }}
    >
      <div style={{
        background: C.subBg,
        padding: '18px 30px',
        borderRadius: 14,
        maxWidth: '92%',
        textAlign: 'center',
        opacity: op,
        borderLeft: `5px solid ${C.accent}`,
      }}>
        <span style={{
          fontSize: 48,
          fontWeight: 700,
          color: C.white,
          lineHeight: 1.45,
          textShadow: '0 2px 10px rgba(0,0,0,0.7)',
          fontFamily: "'Noto Sans Tamil', 'Noto Sans', system-ui, sans-serif",
        }}>
          {current.text}
        </span>
      </div>
    </AbsoluteFill>
  );
};

// ── Progress Bar ─────────────────────────────────────────────────────────────
const ProgressBar = () => {
  const f = useCurrentFrame();
  const pct = (f / TOTAL) * 100;
  return (
    <AbsoluteFill style={{ pointerEvents: 'none' }}>
      <div style={{
        position: 'absolute', bottom: 0, left: 0, right: 0,
        height: 6, background: 'rgba(255,255,255,0.12)',
      }}>
        <div style={{
          height: '100%', width: `${pct}%`,
          background: `linear-gradient(90deg,${C.accent},${C.accent2})`,
        }} />
      </div>
    </AbsoluteFill>
  );
};

// ── Logo Watermark ───────────────────────────────────────────────────────────
const LogoWatermark = ({ name }) => {
  const f = useCurrentFrame();
  const op = interpolate(f, [0, 18], [0, 0.88], { extrapolateRight: 'clamp' });
  return (
    <AbsoluteFill style={{ pointerEvents: 'none' }}>
      <div style={{
        position: 'absolute', top: 52, left: 42,
        display: 'flex', alignItems: 'center', gap: 10,
        opacity: op,
      }}>
        <div style={{ width: 10, height: 38, background: C.accent, borderRadius: 5 }} />
        <span style={{
          fontSize: 28, fontWeight: 800, color: C.white,
          textShadow: '0 2px 10px rgba(0,0,0,0.8)',
          letterSpacing: '0.3px',
        }}>
          {name || 'I Have a Cause'}
        </span>
      </div>
    </AbsoluteFill>
  );
};

// ── Branding Scene (frames 1710–1799) ────────────────────────────────────────
const BrandingScene = ({ channelName, language }) => {
  const f = useCurrentFrame();
  const op     = fadeIn(f, 16);
  const sc     = spring({ frame: f, fps: FPS, from: 0.82, to: 1.0, config: { damping: 12 } });
  const btnOp  = interpolate(f, [18, 32], [0, 1], { extrapolateRight: 'clamp' });
  const subText = language === 'tamil' ? 'Subscribe பண்ணுங்க! 🔔' : 'Subscribe Now! 🔔';

  return (
    <AbsoluteFill
      style={{
        background: 'linear-gradient(160deg,#0d0f14 0%,#1a0808 100%)',
        display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center',
        gap: 36, opacity: op,
        padding: '0 50px',
      }}
    >
      {/* Channel name */}
      <div style={{ transform: `scale(${sc})`, textAlign: 'center' }}>
        <div style={{
          fontSize: 56, fontWeight: 900, color: C.white,
          letterSpacing: '-0.5px',
        }}>
          I Have a{' '}
          <span style={{ color: C.accent }}>Cause</span>
        </div>
        <div style={{
          fontSize: 30, fontWeight: 500,
          color: 'rgba(255,255,255,0.55)',
          marginTop: 12,
        }}>
          Tamil News · Unfiltered Truth
        </div>
      </div>

      {/* Subscribe CTA */}
      <div style={{
        padding: '20px 52px',
        background: C.accent,
        borderRadius: 50,
        opacity: btnOp,
        boxShadow: `0 8px 32px rgba(232,65,42,0.45)`,
      }}>
        <span style={{ fontSize: 38, fontWeight: 800, color: C.white }}>
          {subText}
        </span>
      </div>
    </AbsoluteFill>
  );
};

// ── Main Export ───────────────────────────────────────────────────────────────
export const VideoShort = ({ data }) => {
  const f = useCurrentFrame();
  const scenes    = (data.scenes || []).slice(0, 4);
  const subtitles = data.subtitles || [];

  return (
    <AbsoluteFill style={{
      backgroundColor: C.dark,
      fontFamily: "'Noto Sans Tamil','Noto Sans',system-ui,sans-serif",
    }}>

      {/* Background music (low volume throughout) */}
      {data.musicFile && (
        <Audio src={staticFile(data.musicFile)} volume={0.08} />
      )}

      {/* Voice narration — starts after hook (frame 90) */}
      {data.audioFile && (
        <Sequence from={HOOK_END}>
          <Audio src={staticFile(data.audioFile)} />
        </Sequence>
      )}

      {/* ── Hook (0–89) ── */}
      <Sequence from={0} durationInFrames={HOOK_END}>
        <HookScene hookText={data.hookText} />
      </Sequence>

      {/* ── Scenes ── */}
      {scenes.map((scene, i) => (
        <Sequence
          key={i}
          from={SCENE_STARTS[i]}
          durationInFrames={SCENE_DURATIONS[i]}
        >
          <StoryScene
            scene={scene}
            sceneIndex={i}
            duration={SCENE_DURATIONS[i]}
          />
        </Sequence>
      ))}

      {/* ── Branding (1710–1799) ── */}
      <Sequence from={BRAND_START} durationInFrames={TOTAL - BRAND_START}>
        <BrandingScene channelName={data.channelName} language={data.language} />
      </Sequence>

      {/* ── Subtitles (during scenes only) ── */}
      {f >= HOOK_END && f < BRAND_START && (
        <SubtitleLayer subtitles={subtitles} />
      )}

      {/* ── Progress bar ── */}
      <ProgressBar />

      {/* ── Logo watermark (hidden during branding) ── */}
      {f < BRAND_START && <LogoWatermark name={data.channelName} />}
    </AbsoluteFill>
  );
};
