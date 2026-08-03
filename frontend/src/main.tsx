import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'

const root = createRoot(document.getElementById('root')!)

// Standalone routes render on their own, bypassing the Stream chat client so they can be viewed
// without auth. The channel id in the query selects which labeler to fetch:
//   `?preview=<channel_id>` → the preview stage (GET /labeler-spec, /preview-posts)
//   `?guide=<channel_id>`   → the opt-out maintenance guide (GET /maintenance-guide)
const params = new URLSearchParams(window.location.search)
const isPreview =
  params.has('preview') || window.location.hash.replace(/^#/, '') === 'preview'
const isGuide = params.has('guide')

if (isGuide) {
  import('./components/MaintenanceGuide').then(({ MaintenanceGuide }) => {
    root.render(
      <StrictMode>
        <MaintenanceGuide channelId={params.get('guide') ?? ''} />
      </StrictMode>,
    )
  })
} else if (isPreview) {
  import('./components/LabelerPreview').then(({ LabelerPreview }) => {
    root.render(
      <StrictMode>
        <LabelerPreview channelId={params.get('preview') ?? ''} />
      </StrictMode>,
    )
  })
} else {
  import('./App.tsx').then(({ default: App }) => {
    root.render(
      <StrictMode>
        <App />
      </StrictMode>,
    )
  })
}
