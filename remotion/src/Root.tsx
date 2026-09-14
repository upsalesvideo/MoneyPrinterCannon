import React from 'react';
import {CalculateMetadataFunction, Composition} from 'remotion';
import {Video} from './Video';
import {demoProps} from './demoProps';
import {VideoProps} from './theme';

const calculateMetadata: CalculateMetadataFunction<VideoProps> = ({props}) => {
  const fps = props.fps || 30;
  const width = props.width || (props.aspect === '16:9' ? 1920 : 1080);
  const height =
    props.height || (props.aspect === '16:9' ? 1080 : props.aspect === '1:1' ? 1080 : 1920);
  const durationInFrames = Math.max(1, Math.ceil((props.durationSec || 1) * fps));
  return {fps, width, height, durationInFrames, props};
};

export const Root: React.FC = () => (
  <Composition
    id="Main"
    component={Video}
    defaultProps={demoProps}
    calculateMetadata={calculateMetadata}
    width={demoProps.width}
    height={demoProps.height}
    fps={demoProps.fps}
    durationInFrames={Math.ceil(demoProps.durationSec * demoProps.fps)}
  />
);
