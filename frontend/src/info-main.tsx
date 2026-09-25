import { StrictMode, type ComponentType } from "react"
import { createRoot } from "react-dom/client"
import "./index.css"
import { AboutPage } from "./pages/about"
import { ContactPage } from "./pages/contact"
import { FaqPage } from "./pages/faq"
import { JoinPage } from "./pages/join"
import { NotFoundPage } from "./pages/not-found"
import { PrivacyPage } from "./pages/privacy"

const PAGES: Record<string, ComponentType> = {
  about: AboutPage,
  contact: ContactPage,
  faq: FaqPage,
  join: JoinPage,
  privacy: PrivacyPage,
}

// GitHub Pages serves both /about.html and the extensionless /about.
const pageName = window.location.pathname.split("/").pop()?.replace(/\.html$/, "")
const Page = pageName && PAGES[pageName] ? PAGES[pageName] : NotFoundPage

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Page />
  </StrictMode>
)
