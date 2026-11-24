import { ApiError, requestJson } from './client'
import { mediaUrl } from '../../src/api/client'
import { formatBookAge } from '../../src/data/bookMetadata'

function toBook(version, placeholder) {
  return {
    ...version,
    age: formatBookAge(version),
    cover: mediaUrl(version.coverRef, 'thumbnail') || placeholder,
    pages: version.pages.map((page) => ({
      page: page.pageNumber,
      text: page.text,
      image: mediaUrl(page.imageRef, 'reading') || placeholder,
      defaultNextPageNumber: page.defaultNextPageNumber,
      tokens: Array.from(page.text, (char, index) => ({ char, index })),
    })),
  }
}

export class LiveBooksApi {
  constructor(placeholder, request = requestJson) {
    this.placeholder = placeholder
    this.request = request
  }

  async list(page, pageSize) {
    const query = new URLSearchParams({ page: String(page), pageSize: String(pageSize) })
    const { data } = await this.request(`/api/v1/storybooks?${query}`)
    return data.items.map((version) => toBook(version, this.placeholder))
  }

  async get(storyVersionId) {
    const { data } = await this.request(`/api/v1/story-versions/${storyVersionId}`)
    if (data?.storyVersionId !== storyVersionId || !data.pages?.length) {
      throw new ApiError('服务端未返回该绘本版本的可读页面', { code: 'INVALID_STORY_VERSION' })
    }
    return toBook(data, this.placeholder)
  }
}
