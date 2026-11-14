import React from "react"
import ReactDOM from "react-dom/client"
import "./index.scss"
import { BrowserRouter } from "react-router"
import AppRouter from "./Router"

const root = ReactDOM.createRoot(document.getElementById("root") as HTMLElement)

root.render(
    <React.StrictMode>
        <div className="app">
            <BrowserRouter>
                <AppRouter />
            </BrowserRouter>
        </div>
    </React.StrictMode>,
)
