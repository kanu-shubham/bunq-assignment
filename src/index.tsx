import React from 'react';
import ReactDOM from 'react-dom';
import App from './App';
import HitlLearnWorkbench from './features/HitlLearnWorkbench';

// Visit "/?learn" to open the annotated teaching workbench (interview demo).
const Root =
  typeof window !== 'undefined' && window.location.search.includes('learn')
    ? HitlLearnWorkbench
    : App;

ReactDOM.render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
  document.getElementById('root'),
);
