import { describe, expect, it } from 'vitest';
import { SseParser } from '../api/sse';

describe('SSE parser', () => {
  it('handles arbitrary chunk boundaries, ids, comments and multi-line data', () => {
    const parser = new SseParser();
    expect(parser.push(': keepalive\n\nid: 1\nev')).toEqual([]);
    const frames = [
      ...parser.push('ent: message.created\ndata: {"id":1,"type":"message.created","text":"he'),
      ...parser.push('llo"}\n\nid: 2\ndata: line1\ndata: line2\n\n'),
    ];
    expect(frames).toHaveLength(2);
    expect(frames[0]).toMatchObject({ id: 1, event: 'message.created' });
    expect(JSON.parse(frames[0].data)).toMatchObject({ id: 1, text: 'hello' });
    expect(frames[1]).toMatchObject({ id: 2, event: 'message' });
    expect(frames[1].data).toBe('line1\nline2');
  });

  it('ignores comment-only frames and malformed ids', () => {
    const parser = new SseParser();
    expect(parser.push(': ping\n\n')).toEqual([]);
    const frames = parser.push('id: nope\nevent: error\ndata: {"message":"x"}\n\n');
    expect(frames[0].id).toBeNull();
    expect(frames[0].event).toBe('error');
  });
});
