<script setup>
import { computed, onBeforeUnmount, onMounted } from 'vue'
import AppHeader from './components/AppHeader.vue'
import AuthPage from './pages/AuthPage.vue'
import HomePage from './pages/HomePage.vue'
import LibraryPage from './pages/LibraryPage.vue'
import BookDetailPage from './pages/BookDetailPage.vue'
import ReaderPage from './pages/ReaderPage.vue'
import AccountPage from './pages/AccountPage.vue'
import { activeProfile, totals, zhiya, zhiyaActions } from './stores/useZhiya'

const state = zhiya
const selectedBook = computed(() =>
  state.books.find((book) => book.id === state.selectedBookId) || state.books[0] || null,
)
const detailBook = computed(() =>
  state.currentBook?.storyVersionId === state.route.versionId ? state.currentBook : null,
)

async function handleProfileSave({ id, payload }) {
  if (id) return zhiyaActions.editProfile(id, payload)
  return zhiyaActions.addProfile(payload)
}

async function startBook(book) {
  try {
    await zhiyaActions.startReading(book)
  } catch (error) {
    zhiyaActions.setToast(error.message || '无法开始阅读', 'error')
  }
}

function openDetail(book) {
  if (!book?.storyVersionId) return
  zhiyaActions.navigate(`detail/${book.storyVersionId}`)
}

function onHashChange() {
  zhiyaActions.syncRoute()
}

onMounted(() => {
  window.addEventListener('hashchange', onHashChange)
  zhiyaActions.bootstrap()
})

onBeforeUnmount(() => window.removeEventListener('hashchange', onHashChange))
</script>

<template>
  <div class="app">
    <AppHeader
      :route="state.route"
      :auth="state.auth"
      @navigate="zhiyaActions.navigate"
    />

    <AuthPage
      v-if="state.route.name === 'auth' || (state.route.name === 'account' && !state.auth.authenticated)"
      :health="state.health"
      :login="zhiyaActions.signIn"
      :register="zhiyaActions.signUp"
      @back="zhiyaActions.navigate('home')"
    />
    <HomePage
      v-else-if="state.route.name === 'home'"
      :books="state.books"
      :selected-book-id="state.selectedBookId"
      :active-profile="activeProfile()"
      :auth="state.auth"
      :books-source="state.booksSource"
      :books-error="state.booksError"
      @select="zhiyaActions.selectBook"
      @detail="openDetail"
      @start="startBook"
      @navigate="zhiyaActions.navigate"
    />
    <LibraryPage
      v-else-if="state.route.name === 'library'"
      :books="state.books"
      :selected-id="state.selectedBookId"
      :loading="state.booksLoading"
      :has-more="state.nextBooksPage !== null"
      :source="state.booksSource"
      :error="state.booksError"
      @select="zhiyaActions.selectBook"
      @detail="openDetail"
      @start="startBook"
      @filter="zhiyaActions.loadBooks"
      @more="zhiyaActions.loadMoreBooks"
      @login="zhiyaActions.navigate('auth')"
    />
    <BookDetailPage
      v-else-if="state.route.name === 'detail'"
      :book="detailBook"
      :loading="state.detailLoading"
      @back="zhiyaActions.navigate('library')"
      @start="startBook"
    />
    <ReaderPage
      v-else-if="state.route.name === 'reader'"
      :book="state.currentBook"
      :session="state.currentSession"
      :report="state.currentReport"
      :loading="state.sessionLoading"
      :authenticated="state.auth.authenticated && state.auth.status === 'authenticated'"
      :sync-required="state.readingSyncRequired"
      :record-page="zhiyaActions.recordPageChange"
      :complete-reading="zhiyaActions.completeReading"
      :refresh-reading="zhiyaActions.refreshReading"
      @back="openDetail"
      @library="zhiyaActions.navigate('library')"
      @start-session="zhiyaActions.loadReaderRoute"
    />
    <AccountPage
      v-else-if="state.route.name === 'account'"
      :section="state.route.section"
      :auth="state.auth"
      :profiles="state.profiles"
      :active-profile-id="state.activeProfileId"
      :sessions="state.sessions"
      :books="state.books"
      :totals="totals"
      :loading="state.sessionsLoading"
      @navigate="zhiyaActions.navigate"
      @select-profile="zhiyaActions.selectProfile"
      :save-profile-action="handleProfileSave"
      @sign-out="zhiyaActions.signOut"
      @start="startBook"
    />
    <HomePage
      v-else
      :books="state.books"
      :selected-book-id="state.selectedBookId"
      :active-profile="activeProfile()"
      :auth="state.auth"
      :books-source="state.booksSource"
      :books-error="state.booksError"
      @select="zhiyaActions.selectBook"
      @detail="openDetail"
      @start="startBook"
      @navigate="zhiyaActions.navigate"
    />

    <div class="toast" :class="{ show: state.toast.visible, error: state.toast.tone === 'error' }" role="status">
      {{ state.toast.message }}
    </div>
  </div>
</template>
