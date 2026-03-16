import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Lumen - Semantic Media Search',
  description: 'Search your personal media archive with natural language queries',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body className="bg-gray-900 text-white">
        <nav className="bg-gray-800 border-b border-gray-700 px-6 py-4">
          <div className="max-w-7xl mx-auto">
            <h1 className="text-2xl font-bold bg-gradient-to-r from-blue-400 to-purple-500 bg-clip-text text-transparent">
              🎬 Lumen
            </h1>
            <p className="text-sm text-gray-400 mt-1">Semantic Media Pipeline</p>
          </div>
        </nav>
        <main className="min-h-screen bg-gray-900">
          {children}
        </main>
        <footer className="bg-gray-800 border-t border-gray-700 px-6 py-4 text-center text-sm text-gray-400">
          <p>Distributed semantic indexing for 500GB+ personal media archives</p>
          <p className="mt-1">
            Created by{' '}
            <a
              href="https://danhle.net"
              target="_blank"
              rel="noopener noreferrer"
              className="text-gray-300 hover:text-white underline transition-colors"
            >
              danhle.net
            </a>
          </p>
        </footer>
      </body>
    </html>
  )
}
