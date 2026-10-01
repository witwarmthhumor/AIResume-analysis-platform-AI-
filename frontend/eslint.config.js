// ESLint 扁平配置（v4.3 P1-5a 前端工程化第一块）：
// vue3-recommended 兜住模板层错误（未用组件、重复 key、v-for 缺 key 等），
// 存量误报按需关闭并注释原因，新增规则先本地跑通再收紧。
import pluginVue from 'eslint-plugin-vue'

export default [
  {
    ignores: ['dist/**', 'node_modules/**', 'coverage/**'],
  },
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['**/*.vue', '**/*.js'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        // 浏览器运行时（本项目无 SSR/Node 端代码）
        window: 'readonly',
        document: 'readonly',
        navigator: 'readonly',
        localStorage: 'readonly',
        setTimeout: 'readonly',
        clearTimeout: 'readonly',
        setInterval: 'readonly',
        clearInterval: 'readonly',
        EventSource: 'readonly',
        AbortController: 'readonly',
        fetch: 'readonly',
        FormData: 'readonly',
        alert: 'readonly',
        confirm: 'readonly',
        requestAnimationFrame: 'readonly',
        getComputedStyle: 'readonly',
        ResizeObserver: 'readonly',
        IntersectionObserver: 'readonly',
        history: 'readonly',
        location: 'readonly',
        console: 'readonly',
      },
    },
    rules: {
      // 单文件组件命名沿用现状（App.vue、Icon.vue 等单词名）
      'vue/multi-word-component-names': 'off',
      // 属性换行差异较大，交给 ruff format 心智模型：不强制
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/html-self-closing': 'off',
      // 本项目未用 prettier，模板缩进按现状放行
      'vue/html-indent': 'off',
      'vue/html-closing-bracket-newline': 'off',
      'vue/first-attribute-linebreak': 'off',
    },
  },
]
