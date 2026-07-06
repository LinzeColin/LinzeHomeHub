import './styles/tokens.css';
import './styles/base.css';
import './styles/layout.css';
import './styles/scene.css';
import './styles/motion.css';
import './styles/modes.css';
import { initApp } from './app/initApp';

initApp().catch((error) => {
  console.error('Linze Home Hub failed to initialize', error);
});
