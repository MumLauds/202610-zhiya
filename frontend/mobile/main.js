import { createApp } from 'vue'
import App from './App.vue'
import PublicEntry from './PublicEntry.vue'
import './style.css'

createApp(import.meta.env.MODE === 'public' ? PublicEntry : App).mount('#app')
