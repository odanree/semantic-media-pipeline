'use server'

import axios from 'axios'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://api:8000'

export async function embedQuery(query: string): Promise<number[]> {
  const apiKey = process.env.BACKEND_API_KEY || ''
  try {
    const response = await axios.post(`${API_URL}/api/embed-text`, {
      query,
    }, {
      headers: { ...(apiKey && { 'X-API-Key': apiKey }) },
    })
    return response.data.embedding
  } catch (error) {
    console.error('Error embedding query:', error)
    throw new Error('Failed to embed query')
  }
}

export async function searchMedia(
  query: string,
  limit: number = 20,
  threshold: number = 0.3
) {
  const apiKey = process.env.BACKEND_API_KEY || ''
  try {
    const response = await axios.post(`${API_URL}/api/search`, {
      query,
      limit,
      threshold,
    }, {
      headers: { ...(apiKey && { 'X-API-Key': apiKey }) },
    })
    return response.data
  } catch (error) {
    console.error('Error searching media:', error)
    throw new Error('Failed to search media')
  }
}
