import { NextRequest, NextResponse } from 'next/server'

export async function GET() {
  const apiUrl = process.env.API_URL || 'http://api:8000'
  
  return NextResponse.json({
    apiUrl,
    timestamp: new Date().toISOString(),
    nodeEnv: process.env.NODE_ENV,
  })
}
