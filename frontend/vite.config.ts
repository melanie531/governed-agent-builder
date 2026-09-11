import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({build:{target:'es2022'},plugins:[react()],server:{host:'127.0.0.1',proxy:{'/studio-config.json':{target:'http://127.0.0.1:5187',changeOrigin:true},'/api':{target:'http://127.0.0.1:5187',changeOrigin:true}}}});
