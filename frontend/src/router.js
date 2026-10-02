// 路由（v4.3 P1-5c 前端工程化）：hash 模式——深链、刷新、浏览器后退不再丢视图，
// 无需后端 SPA fallback 配置。四业务模块静态打包走首屏；收起的旧视图（在线对话/
// AI 客服/使用日志/管理端/语料库）按需分包（代码保留、不在导航露出，方案 §2.2）。
import { createRouter, createWebHashHistory } from 'vue-router'
import ResumeView from './components/ResumeView.vue'
import InterviewView from './components/InterviewView.vue'
import QuestionGenView from './components/QuestionGenView.vue'
import AudioView from './components/AudioView.vue'

const routes = [
  { path: '/', redirect: '/resume' },
  { path: '/resume', component: ResumeView, meta: { key: 'resume' } },
  { path: '/audio', component: AudioView, meta: { key: 'audio' } },
  { path: '/question-gen', component: QuestionGenView, meta: { key: 'question-gen' } },
  { path: '/interview', component: InterviewView, meta: { key: 'interview' } },
  {
    path: '/history',
    component: () => import('./components/HistoryView.vue'),
    meta: { key: 'history' },
  },
  {
    path: '/admin',
    component: () => import('./components/AdminPanel.vue'),
    meta: { key: 'admin', wide: true },
  },
  {
    path: '/kb-admin',
    component: () => import('./components/KbAdminView.vue'),
    meta: { key: 'kb-admin' },
  },
  {
    path: '/chat',
    component: () => import('./components/ChatView.vue'),
    meta: { key: 'chat' },
  },
  {
    path: '/agent',
    component: () => import('./components/agent/AgentChatView.vue'),
    meta: { key: 'agent', wide: true },
  },
  { path: '/:pathMatch(.*)*', redirect: '/resume' },
]

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
})
