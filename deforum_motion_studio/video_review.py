"""Review-video reader for the render/refine loop.

Loads a rendered Deforum video and serves individual frames by index, so the
app can show the exact rendered frame on the same timeline as the wireframe
preview and the motion curves. Playback is sequential (fast path); scrubbing
seeks to the nearest keyframe and decodes forward, with a small LRU cache so
back-and-forth review stays smooth.
"""
import cv2
import numpy as np
from collections import OrderedDict

class VideoReview:
    def __init__(self,path,cache=16):
        self.cap=cv2.VideoCapture(str(path))
        if not self.cap.isOpened():
            raise ValueError(f'Could not open video: {path}')
        self.n=max(1,int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)))
        self.fps=float(self.cap.get(cv2.CAP_PROP_FPS) or 0.0)
        self.width=int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height=int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if self.width<=0 or self.height<=0:
            self.cap.release();raise ValueError(f'No video stream in: {path}')
        self._cache=OrderedDict();self._cache_size=max(4,int(cache));self._seq=-1
    def frame(self,i):
        """RGB frame at index i (clamped to the stream). None if decode fails."""
        i=max(0,min(self.n-1,int(i)))
        hit=self._cache.get(i)
        if hit is not None:
            self._cache.move_to_end(i);return hit
        if i!=self._seq+1:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES,i)
        ok,bgr=self.cap.read()
        if not ok:return None
        self._seq=i
        rgb=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
        self._cache[i]=rgb
        while len(self._cache)>self._cache_size:self._cache.popitem(last=False)
        return rgb
    def close(self):
        try:self.cap.release()
        except Exception:pass
