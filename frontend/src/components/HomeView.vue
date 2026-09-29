<script setup>
/* HomeView —— 首页落地页（v4.1 A4 导航收敛）。
   导航收敛后首页只承担「分流」职责：两张业务入口卡（简历评估 / 模拟面试），
   业务功能分别移入 ResumeView / InterviewView。点击卡片 emit navigate 交由 App 切视图。 */
const emit = defineEmits(['navigate'])
</script>

<template>
  <section class="landing">
    <p class="tagline">
      <b>上传简历</b> · AI 深度分析 · <b>模拟实战面试</b> —— 求职路上的私人面试官
    </p>

    <div class="entry-row">
      <button class="entry-card resume" @click="emit('navigate', 'resume')">
        <span class="entry-icon">📄</span>
        <span class="entry-title">简历评估</span>
        <span class="entry-desc">上传简历 PDF / Word，AI 逐维度生成评估报告：岗位匹配度、优势短板、量化建议</span>
        <span class="entry-go">进入 →</span>
      </button>
      <button class="entry-card audio" @click="emit('navigate', 'audio')">
        <span class="entry-icon">🎧</span>
        <span class="entry-title">录音分析</span>
        <span class="entry-desc">上传面试录音：本地转写文本 → 角色审核标注 → 四维评分的面试审核复盘</span>
        <span class="entry-go">进入 →</span>
      </button>
      <button class="entry-card qgen" @click="emit('navigate', 'question-gen')">
        <span class="entry-icon">📝</span>
        <span class="entry-title">面试题生成</span>
        <span class="entry-desc">基于简历内容生成 10~20 道定制化面试题，保存为题库供模拟面试加载</span>
        <span class="entry-go">进入 →</span>
      </button>
      <button class="entry-card interview" @click="emit('navigate', 'interview')">
        <span class="entry-icon">🎤</span>
        <span class="entry-title">模拟面试</span>
        <span class="entry-desc">选简历（可选加载题库）多轮实战问答，结束生成四维评分复盘，checkpoint 断点续跑</span>
        <span class="entry-go">进入 →</span>
      </button>
    </div>

    <p class="hint">💡 提示：业务功能均需登录使用；历史记录会保存在你的账号下，换设备登录也能找回。</p>
  </section>
</template>

<style scoped>
.landing {
  display: flex;
  flex-direction: column;
  gap: 22px;
  padding-top: 4vh;
}
.tagline {
  text-align: center;
  font-size: 13px;
  color: var(--c-muted);
  letter-spacing: 0.5px;
  margin: 2px 0 0;
}
.tagline b {
  color: var(--c-primary-dark);
  font-weight: 600;
}
.entry-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px;
}
.entry-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  text-align: left;
  border: 1px solid var(--c-border);
  border-radius: 16px;
  background: #fff;
  padding: 26px 24px;
  font-family: inherit;
  cursor: pointer;
  transition: transform 0.16s ease, box-shadow 0.16s ease, border-color 0.16s ease;
}
.entry-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 12px 30px rgba(15, 118, 110, 0.12);
  border-color: #6ee7b7;
}
.entry-card.resume { --entry: #10b981; }
.entry-card.audio { --entry: #06b6d4; }
.entry-card.qgen { --entry: #8b5cf6; }
.entry-card.interview { --entry: #f59e0b; }
.entry-icon {
  font-size: 30px;
  line-height: 1;
}
.entry-title {
  font-size: 17px;
  font-weight: 700;
  color: var(--c-text);
}
.entry-desc {
  font-size: 12.5px;
  line-height: 1.8;
  color: var(--c-muted);
}
.entry-go {
  margin-top: 6px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--entry);
}
.hint {
  text-align: center;
  font-size: 12px;
  color: var(--c-faint);
  margin: 4px 0 0;
}
@media (max-width: 700px) {
  .entry-row { grid-template-columns: 1fr; }
}
</style>
