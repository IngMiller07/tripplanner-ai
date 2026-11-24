// Configuracion publica del frontend. NO hay credenciales de AWS aqui:
//  - API_URL    -> API Gateway (Canal 1: Dashboard, viajes, itinerarios, reservas)
//  - STREAM_URL -> Lambda Function URL con streaming (Canal 2: asistente, vuelos, clima)
// deploy-frontend.ps1 la reescribe con los valores del stack de CloudFormation.
window.TRIPPLANNER_CONFIG = {
  API_URL: "",
  STREAM_URL: "",
};
