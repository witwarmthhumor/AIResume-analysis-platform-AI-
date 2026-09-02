// 前端总入口：创建 Vue 应用并挂载到 index.html 的 #app
import { createApp } from 'vue'
import App from './App.vue'
import './assets/main.css' // 全局设计系统：变量 + 通用类 + 动效

createApp(App).mount('#app')
