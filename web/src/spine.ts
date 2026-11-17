// Whether a point is near a polyline's vertices: the spine's projected center line is sampled densely enough that
// its vertices stand for the line.
export interface Point { x: number; y: number }

export const nearLine = (line: Point[], x: number, y: number, radius: number): boolean =>
  line.some((p) => Math.hypot(p.x - x, p.y - y) < radius);
