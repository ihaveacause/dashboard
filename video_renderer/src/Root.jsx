import React from 'react';
import { Composition } from 'remotion';
import { VideoShort } from './VideoShort.jsx';

export const RemotionRoot = () => {
  return (
    <Composition
      id="VideoShort"
      component={VideoShort}
      durationInFrames={1800}
      fps={30}
      width={1080}
      height={1920}
      defaultProps={{
        data: {
          hookText: 'Breaking News',
          storyTitle: 'Preview Story',
          language: 'english',
          audioFile: null,
          musicFile: null,
          subtitles: [],
          scenes: [
            { videoFile: null, textOverlay: 'Scene 1' },
            { videoFile: null, textOverlay: 'Scene 2' },
            { videoFile: null, textOverlay: 'Scene 3' },
            { videoFile: null, textOverlay: 'Scene 4' },
          ],
          channelName: 'I Have a Cause',
          niche: 'general',
        },
      }}
    />
  );
};
