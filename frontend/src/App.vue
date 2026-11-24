<script setup>
import { computed, defineAsyncComponent, h, onBeforeUnmount, onMounted } from 'vue'
import AppHeader from './components/AppHeader.vue'
import AuthPage from './pages/AuthPage.vue'
import HomePage from './pages/HomePage.vue'
import LibraryPage from './pages/LibraryPage.vue'
import BookDetailPage from './pages/BookDetailPage.vue'
import ReaderPage from './pages/ReaderPage.vue'
import AccountPage from './pages/AccountPage.vue'
import ChildModePage from './pages/ChildModePage.vue'
import { activeProfile, totals, zhiya, zhiyaActions } from './stores/useZhiya'
import { mapApiBook } from './data/preview'

const loadingPage = { render: () => h('p', { role: 'status' }, '正在载入页面…') }
const unavailablePage = { render: () => h('p', { role: 'alert' }, '页面加载失败，请刷新重试。') }
const AdminPage = defineAsyncComponent({ loader: () => import('./pages/AdminPage.vue'), loadingComponent: loadingPage, errorComponent: unavailablePage })
const WorkshopPage = defineAsyncComponent({ loader: () => import('./pages/WorkshopPage.vue'), loadingComponent: loadingPage, errorComponent: unavailablePage })
const state = zhiya
const publicSite = import.meta.env.MODE === 'public'
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

async function startBook(book, options) {
  try {
    await zhiyaActions.startReading(book, options)
  } catch (error) {
    zhiyaActions.setToast(error.message || '无法开始阅读', 'error')
  }
}

async function startWorkshopBook(version) {
  return startBook(mapApiBook(version))
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
    v-if="state.route.name === 'child' && (!publicSite || ['child', 'handoff', 'verifying-handoff'].includes(state.auth.status))"
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
      v-if="!publicSite || state.auth.authenticated"
      :route="state.route"
      :auth="state.auth"
      @navigate="zhiyaActions.navigate"
    />

    <AuthPage
      v-if="state.route.name === 'auth' || (!state.auth.authenticated && (publicSite || ['account', 'admin', 'workshop'].includes(state.route.name)))"
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
      :starting="state.sessionBusy"
      :books="state.books"
      :filters="state.bookFilters"
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
      @workshop="zhiyaActions.navigate('workshop')"
    />
    <WorkshopPage
      v-else-if="state.route.name === 'workshop'"
      :workshop="state.workshop"
      :auth="state.auth"
      :active-profile="activeProfile()"
      :source-session-id="state.route.sourceSessionId"
      :generate-action="zhiyaActions.generateWorkshopDraft"
      :retry-generation-action="zhiyaActions.retryWorkshopGeneration"
      :reset-action="zhiyaActions.resetWorkshopDraft"
      :approve-action="zhiyaActions.approveWorkshopDraft"
      :open-shelf-action="zhiyaActions.openApprovedPrivateShelf"
      :continue-reading-action="zhiyaActions.switchWorkshopContinuation"
      @start-story="startWorkshopBook"
      @navigate="zhiyaActions.navigate"
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
      @setup-child="book => startBook(book, { childSetup: true })"
    />
    <ReaderPage
      v-else-if="state.route.name === 'reader'"
      :book="state.currentBook"
      :session="state.currentSession"
      :events="state.currentEvents"
      :report="state.currentReport"
      :loading="state.sessionLoading || state.auth.status === 'checking'"
      :authenticated="state.auth.authenticated && state.auth.status === 'authenticated'"
      :account-id="state.auth.accountId"
      :sync-required="state.readingSyncRequired"
      :can-retry-load="state.readerLoadRetryable"
      :request-guidance="zhiyaActions.requestGuidance"
      :submit-answer="zhiyaActions.submitAnswer"
      :ask-question="zhiyaActions.askQuestion"
      :choose-branch="zhiyaActions.chooseBranch"
      :record-page="zhiyaActions.recordPageChange"
      :complete-reading="zhiyaActions.completeReading"
      :refresh-reading="zhiyaActions.refreshReading"
      :available-books="state.books.filter((book) => book.source === 'api')"
      :enter-child-mode-action="zhiyaActions.enterChildMode"
      @continue-story="state.currentSession?.readingSessionId && zhiyaActions.navigate(`workshop/continue/${state.currentSession.readingSessionId}`)"
      @back="zhiyaActions.openDetail"
      @library="zhiyaActions.navigate('library')"
      :child-setup="state.route.childSetup"
      @retry-start="zhiyaActions.startCurrentReading"
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
      :more-sessions="Boolean(state.nextSessionsPage)"
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
      @reload-sessions="zhiyaActions.loadSessions()"
      @more-sessions="zhiyaActions.loadSessions({ append: true })"
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
      @more="section => zhiyaActions.loadAdminContent(section, { append: true })"
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
    <template v-if="state.toast.visible && state.toast.tone === 'error'">
      <button v-if="state.readingCreationRecovery" class="text-button" type="button" :disabled="state.sessionBusy"
        @click="zhiyaActions.retryReadingCreation">核对原阅读请求</button>
      <button class="text-button" type="button" aria-label="关闭通知" @click="zhiyaActions.dismissToast">关闭</button>
    </template>
  </div>
</template>
