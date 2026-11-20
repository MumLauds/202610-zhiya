import { createApp } from 'vue'
import App from './App.vue'
import './style.css'

async function start() {
  if (import.meta.env.MODE === 'public') {
    try {
      const response = await fetch('/api/v1/storybooks?pageSize=1', { cache: 'no-store' })
      if (!response.ok) throw new Error('Login required')
    } catch {
      location.replace('/#auth')
      return
    }
  }
  createApp(App).mount('#app')
}
start()
