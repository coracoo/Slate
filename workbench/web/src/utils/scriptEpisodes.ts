// -*- coding: utf-8 -*-
/** 剧本分集的生成状态判断；已有正文的集不进入一键扩写队列。 */
export interface EpisodeLike {
  id: string
  text?: string
}

export function hasEpisodeText(episode: EpisodeLike): boolean {
  return Boolean(String(episode.text || '').trim())
}

export function pendingEpisodeIds(episodes: readonly EpisodeLike[]): string[] {
  return episodes.filter((episode) => !hasEpisodeText(episode)).map((episode) => episode.id)
}
