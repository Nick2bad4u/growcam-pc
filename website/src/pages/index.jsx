import Heading from "@theme/Heading";
import Head from "@docusaurus/Head";
import Layout from "@theme/Layout";
import Link from "@docusaurus/Link";

import styles from "./index.module.css";

const pageDescription = "Open-source VIVOSUN GrowCam C4 desktop viewer with local live video, 24-hour rewind, time-lapse previews, and camera file downloads.";

const structuredData = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: "GrowCam PC",
  applicationCategory: "MultimediaApplication",
  operatingSystem: "Windows, macOS, Linux",
  description: pageDescription,
  url: "https://nick2bad4u.github.io/growcam-pc/",
  downloadUrl: "https://pypi.org/project/growcam-pc/",
  isAccessibleForFree: true,
  offers: {
    "@type": "Offer",
    price: "0",
    priceCurrency: "USD",
  },
};

const features = [
  {
    icon: "◉",
    tone: "cyan",
    title: "Watch locally",
    text: "View the live RTSP feed and save full-resolution snapshots without a vendor cloud relay.",
  },
  {
    icon: "↶",
    tone: "amber",
    title: "Rewind the day",
    text: "Scrub across continuous camera recordings and open short, browser-ready preview windows.",
  },
  {
    icon: "◫",
    tone: "violet",
    title: "Track time-lapses",
    text: "Review the native capture schedule and preview every frame collected so far.",
  },
  {
    icon: "⇩",
    tone: "green",
    title: "Browse camera files",
    text: "Search both storage partitions and download closed recordings with safe filenames.",
  },
];

function CameraMockup() {
  return (
    <div className={styles.cameraShell} aria-label="GrowCam PC dashboard preview">
      <div className={styles.cameraTopbar}>
        <span><i className={styles.liveDot} /> LIVE</span>
        <span className={styles.connected}>Connected</span>
      </div>
      <div className={styles.cameraView}>
        <div className={styles.plantStem} />
        <div className={`${styles.leaf} ${styles.leafOne}`} />
        <div className={`${styles.leaf} ${styles.leafTwo}`} />
        <div className={`${styles.leaf} ${styles.leafThree}`} />
        <div className={styles.scanline} />
      </div>
      <div className={styles.cameraStats}>
        <span><strong>24h</strong> rewind</span>
        <span><strong>60%</strong> free</span>
        <span><strong>5m</strong> preview</span>
      </div>
    </div>
  );
}

function Home() {
  return (
    <Layout title="VIVOSUN GrowCam C4 desktop viewer" description={pageDescription}>
      <Head>
        <script type="application/ld+json">{JSON.stringify(structuredData)}</script>
      </Head>
      <main>
        <header className={styles.hero}>
          <div className={styles.heroGlow} />
          <div className={styles.heroInner}>
            <div className={styles.heroCopy}>
              <p className={styles.kicker}>OPEN-SOURCE GROWCAM C4 VIEWER</p>
              <Heading as="h1">Your VIVOSUN GrowCam C4.<br /><span>On your desktop.</span></Heading>
              <p className={styles.lead}>
                Watch live video, rewind a full day, preview time-lapse progress, and download camera files from one private local dashboard.
              </p>
              <div className={styles.actions}>
                <Link className="button button--primary button--lg" to="/docs/getting-started">Install GrowCam PC</Link>
                <Link className="button button--secondary button--lg" to="/docs/dashboard">Explore the dashboard</Link>
              </div>
              <div className={styles.installLine}>
                <code>uv tool install growcam-pc</code>
                <span>Python 3.11+ · Windows · macOS · Linux</span>
              </div>
            </div>
            <CameraMockup />
          </div>
        </header>

        <section className={styles.featureSection}>
          <div className={styles.sectionIntro}>
            <p className={styles.kicker}>ONE LOCAL TOOL</p>
            <Heading as="h2">The useful parts of the camera app, on your PC.</Heading>
          </div>
          <div className={styles.featureGrid}>
            {features.map((feature) => (
              <article className={styles.featureCard} key={feature.title}>
                <span className={`${styles.featureIcon} ${styles[feature.tone]}`}>{feature.icon}</span>
                <Heading as="h3">{feature.title}</Heading>
                <p>{feature.text}</p>
              </article>
            ))}
          </div>
        </section>

        <section className={styles.compatibilitySection}>
          <div>
            <p className={styles.kicker}>TESTED HARDWARE</p>
            <Heading as="h2">Built with the VIVOSUN GrowCam C4.</Heading>
          </div>
          <div className={styles.compatibilityCopy}>
            <p>
              GrowCam PC is developed and protocol-tested with the GrowCam C4 (model VSC-GCC4, product B0D8PQQWM3). It connects directly to the camera's local RTSP and DVRIP services after initial camera setup.
            </p>
            <p>
              Similar XMEye cameras may work, but are not verified. Check the <Link to="/docs/growcam-c4-setup">GrowCam C4 setup guide</Link> for tested features and credential guidance before installing.
            </p>
          </div>
        </section>

        <section className={styles.localSection}>
          <div>
            <p className={styles.kicker}>PRIVATE BY DEFAULT</p>
            <Heading as="h2">The browser talks to your computer. Your computer talks to the camera.</Heading>
          </div>
          <div className={styles.flow} aria-label="Local connection flow">
            <span>Browser</span><b>→</b><span>GrowCam PC</span><b>→</b><span>Camera LAN</span>
          </div>
          <p>No account, hosted dashboard, or remote relay is required. The web server binds to loopback unless you explicitly allow a network bind.</p>
        </section>
      </main>
    </Layout>
  );
}

export default Home;
