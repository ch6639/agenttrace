import { createApp } from 'vue'
import {
  ElAlert, ElButton, ElConfigProvider, ElDatePicker, ElEmpty, ElLoading, ElOption,
  ElPagination, ElSelect, ElTable, ElTableColumn, ElTag,
} from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import './style.css'

const app = createApp(App)
for (const component of [ElAlert, ElButton, ElConfigProvider, ElDatePicker, ElEmpty,
  ElOption, ElPagination, ElSelect, ElTable, ElTableColumn, ElTag]) app.use(component)
app.use(ElLoading).mount('#app')
