<script setup>
import { computed, onBeforeUnmount, onMounted } from 'vue'
import AppHeader from './components/AppHeader.vue'
import AuthPage from './pages/AuthPage.vue'
import HomePage from './pages/HomePage.vue'
import LibraryPage from './pages/LibraryPage.vue'
import BookDetailPage from './pages/BookDetailPage.vue'
import ReaderPage from './pages/ReaderPage.vue'
import AccountPage from './pages/AccountPage.vue'
import AdminPage from './pages/AdminPage.vue'
import ChildModePage from './pages/ChildModePage.vue'
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

function onHashChange() {
  zhiyaActions.syncRoute()
}

onMounted(() => {
  window.addEventListener('hashchange', onHashChange)
  window.addEventListener('focus', zhiyaActions.refreshIdentity)
  window.addEventListener('storage', zhiyaActions.syncStoredIdentity)
  zhiyaActions.bootstrap()
})

onBeforeUnmount(() => {
  window.removeEventListener('hashchange', onHashChange)
  window.removeEventListener('focus', zhiyaActions.refreshIdentity)
  window.removeEventListener('storage', zhiyaActions.syncStoredIdentity)
})
</script>

<template>
  <ChildModePage
    v-if="state.route.name === 'child'"
    :child-mode="state.childMode"
    :session="state.currentSession"
    :book="state.currentBook"
    :auth="state.auth"
    :loading="state.sessionLoading"
    :identity-loading="state.profilesLoading"
    :identity-error="state.profilesError"
    @recheck-identity="zhiyaActions.refreshIdentity"
    :unlock-action="zhiyaActions.unlockChildMode"
  />
  <div v-else :key="state.accountViewKey" class="app">
    <AppHeader
      :route="state.route"
      :auth="state.auth"
      @navigate="zhiyaActions.navigate"
    />

    <AuthPage
      v-if="state.route.name === 'auth' || (['account', 'admin'].includes(state.route.name) && !state.auth.authenticated)"
      :health="state.health"
      :login="zhiyaActions.signIn"
      :register="zhiyaActions.signUp"
      @back="zhiyaActions.navigate('home')"
    />
    <HomePage
      v-else-if="state.route.name === 'home'"
      :books="state.books"
      :selected-book-id="state.manualBookId"
      :active-profile="activeProfile()"
      :auth="state.auth"
      :books-source="state.booksSource"
      :books-error="state.booksError"
      @select="zhiyaActions.selectBook"
      @detail="zhiyaActions.openDetail"
      @start="startBook"
      @navigate="zhiyaActions.navigate"
    />
    <LibraryPage
      v-else-if="state.route.name === 'library'"
      :books="state.books"
      :selected-id="(state.booksSource === 'api' && state.pendingBookRestore) || state.selectedBookId"
      :loading="state.booksLoading"
      :next-page="state.nextBooksPage"
      :source="state.booksSource"
      :error="state.booksError"
      :auth="state.auth"
      :active-profile="activeProfile()"
      :recommendations="state.recommendations"
      :recommendations-loading="state.recommendationsLoading"
      :recommendations-error="state.recommendationsError"
      @select="zhiyaActions.selectBook"
      @detail="zhiyaActions.openDetail"
      @start="startBook"
      @filter="zhiyaActions.loadBooks"
      @more="zhiyaActions.loadMoreBooks"
      @login="zhiyaActions.navigate('auth')"
      @recommend="zhiyaActions.loadRecommendations"
    />
    <BookDetailPage
      v-else-if="state.route.name === 'detail'"
      :book="detailBook"
      :retry-identity="state.auth.status === 'unavailable' && state.route.source !== 'preview' && !!state.route.versionId"
      :checking-identity="state.profilesLoading"
      :loading="state.detailLoading || state.auth.status === 'checking'"
      @retry-identity="zhiyaActions.retryReadingIdentity"
      @back="zhiyaActions.navigate('library')"
      @start="startBook"
    />
    <ReaderPage
      v-else-if="state.route.name === 'reader'"
      :book="state.currentBook"
      :session="state.currentSession"
      :events="state.currentEvents"
      :report="state.currentReport"
      :loading="state.sessionLoading || state.auth.status === 'checking'"
      :authenticated="state.auth.authenticated && state.auth.status === 'authenticated'"
      :sync-required="state.readingSyncRequired"
      :can-retry-load="state.readerLoadRetryable"
      :request-guidance="zhiyaActions.requestGuidance"
      :submit-answer="zhiyaActions.submitAnswer"
      :choose-branch="zhiyaActions.chooseBranch"
      :record-page="zhiyaActions.recordPageChange"
      :complete-reading="zhiyaActions.completeReading"
      :refresh-reading="zhiyaActions.refreshReading"
      :available-books="state.books.filter((book) => book.source === 'api')"
      :enter-child-mode-action="zhiyaActions.enterChildMode"
      @back="zhiyaActions.openDetail"
      @library="zhiyaActions.navigate('library')"
      @start-session="zhiyaActions.startCurrentReading"
      @retry-load="zhiyaActions.loadReaderRoute"
    />
    <AccountPage
      v-else-if="state.route.name === 'account'"
      :section="state.route.section"
      :auth="state.auth"
      :profiles="state.profiles"
      :profiles-error="state.profilesError"
      :profiles-loading="state.profilesLoading"
      :active-profile-id="state.activeProfileId"
      :sessions="state.sessions"
      :sessions-error="state.sessionsError"
      :books="state.books"
      :totals="totals"
      :totals-loaded="state.sessionsLoaded"
      :loading="state.sessionsLoading"
      @navigate="zhiyaActions.navigate"
      @select-profile="zhiyaActions.selectProfile"
      @detail="zhiyaActions.openDetail"
      :save-profile-action="handleProfileSave"
      :delete-profile-action="zhiyaActions.removeProfile"
      :delete-session-action="zhiyaActions.removeReadingSession"
      :check-deletion-action="zhiyaActions.checkDeletion"
      :pending-deletion-checks="state.pendingDeletionChecks"
      :reload-account-data-action="zhiyaActions.reloadProfiles"
      @reload-sessions="zhiyaActions.loadSessions"
      @sign-out="zhiyaActions.signOut"
      @start="startBook"
    />
    <AdminPage
      v-else-if="state.route.name === 'admin'"
      :section="state.route.section"
      :admin="state.admin"
      :create-story-action="zhiyaActions.adminCreateStory"
      :set-story-status-action="zhiyaActions.adminSetStoryStatus"
      :create-knowledge-action="zhiyaActions.adminCreateKnowledge"
      :update-knowledge-action="zhiyaActions.adminUpdateKnowledge"
      @sign-out="zhiyaActions.signOut"
      @navigate="zhiyaActions.navigate"
    />
    <HomePage
      v-else
      :books="state.books"
      :selected-book-id="state.manualBookId"
      :active-profile="activeProfile()"
      :auth="state.auth"
      :books-source="state.booksSource"
      :books-error="state.booksError"
      @select="zhiyaActions.selectBook"
      @detail="zhiyaActions.openDetail"
      @start="startBook"
      @navigate="zhiyaActions.navigate"
    />

  </div>
  <div class="toast" :class="{ show: state.toast.visible, error: state.toast.tone === 'error' }" role="status">
    {{ state.toast.message }}
  </div>
</template>
